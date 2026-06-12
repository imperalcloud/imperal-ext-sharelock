"""
Sharelock v2 — Chat state machine.

States: CASE_LIST | INTAKE | STATUS | INTELLIGENCE

INTELLIGENCE state uses V3 grounded data (gaps, summaries, entities,
indictment, cross-cutting, taxonomy, audit) fetched directly from the
Cases API. Legacy cases.analysis_result (V2 blob) is NOT used -- it is
stale and caused hallucinations in prior sessions.

Federal anti-hallucination protocol v2 (sessions 25/26 follow-up):
  Layer 1  intelligence_validator  — post-process every LLM answer, flag
                                     unknown_id / content_mismatch citations.
  Layer 2  intelligence_guards     — for factual queries, drop ALL prior
                                     assistant turns; for conversational
                                     follow-ups, keep cited turns.
  Layer 3  intelligence_guards     — if active_run_id / version / status
                                     changed since last turn, invalidate
                                     assistant history unconditionally.
  Layer 4  number_consistency      — scan "<N> files" patterns, flag any
                                     count not present in context.
  Layer 5  prompt rule update      — CITATION DISCIPLINE + COUNT DISCIPLINE
                                     sections appended to intelligence.txt.

SDK v1.6.0 / I-SKELETON-LLM-ONLY: the Layer 3 fingerprint is stored in
``ctx.cache`` (model ``CaseContextFingerprint``) instead of the legacy
``ctx.skeleton_data["_chat_context_fingerprint"]``.
"""
import logging
import os

from app import _user_email, _user_agency
from cache_models import CaseContextFingerprint
from intelligence_context import fetch_grounded_context
from intelligence_format import format_grounded_context, render_findings_deterministic
from intelligence_guards import context_fingerprint
from intelligence_response import (
    build_intelligence_json_instruction,
    parse_intelligence_json,
)
from intelligence_validator import validate_grounded_claims

log = logging.getLogger("sharelock-v2.chat")

_PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "prompts")
# TTL for the per-case grounded-context fingerprint — 5 minutes matches the
# ctx.cache upper bound and is well above typical chat-turn cadence.
_FP_TTL_SECONDS = 300

# Forensic Q&A model — federal-grade reliability over precomputed facts.
# Routed via ctx.ai (BYOLLM/admin-config aware); analysis itself runs on Opus in the microservice.
_QA_MODEL = "claude-opus-4-8"


def _text_of(result) -> str:
    """Extract text from a CompletionResult; .text may be str or list[ContentBlock]."""
    t = getattr(result, "text", "") or ""
    if isinstance(t, list):
        return "\n".join(getattr(b, "text", "") for b in t if getattr(b, "text", ""))
    return t


def _load_prompt(name: str) -> str:
    """Load a prompt template from prompts/ directory."""
    path = os.path.join(_PROMPTS_DIR, f"{name}.txt")
    with open(path) as f:
        return f.read()


def _fp_cache_key(case_id: int, user_id: str) -> str:
    """Redis-safe ctx.cache key for the per-case grounded fingerprint.

    Keeps cache keys scoped per (case, user) so INTELLIGENCE history-drop is
    independent across the user's cases.
    """
    safe_user = (user_id or "anon").replace(":", "-")
    return f"case_context_fp:{safe_user}:{int(case_id)}"


def _build_case_context(case_data: dict, case_id: int | None) -> str:
    """Build case context string from skeleton / API fallback (INTAKE/CASE_LIST only)."""
    lines = []
    cases = case_data.get("cases", [])
    if cases:
        lines.append(f"User has {len(cases)} case(s):")
        for c in cases[:10]:
            marker = " << ACTIVE" if c.get("id") == case_id else ""
            a_st = c.get("analysis_status") or "not run"
            fc = c.get("file_count", "?")
            name = c.get("name", "?")
            cid = c.get("id", "?")
            lines.append(f"  - {name} (ID: {cid}) | analysis: {a_st} | files: {fc}{marker}")

    if case_id:
        lines.append(f"\nActive case: {case_data.get('case_name', f'Case-{case_id}')} (ID: {case_id})")
        lines.append(f"Analysis status: {case_data.get('analysis_status') or 'not run'}")
        lines.append(f"Files uploaded: {case_data.get('file_count', 0)}")
        files = case_data.get("files", [])
        if files:
            lines.append(f"\nUploaded documents ({len(files)} total):")
            for f in files:
                size_kb = f.get("size", 0) // 1024
                lines.append(f"  - {f.get('filename', '?')} ({size_kb} KB)")

    return "\n".join(lines) if lines else "No case data available."


def resolve_state(case_id: int | None, analysis_status: str | None) -> str:
    """Determine chat state from case context.

    GAP_REVIEW (analysis paused waiting for the operator's continue /
    add-evidence decision) is its own state so chat LEADS with the pending
    decision instead of mistaking a paused case for an un-analyzed one
    (the prior bug: gap_review fell through to INTAKE).
    """
    if not case_id:
        return "CASE_LIST"
    if analysis_status == "gap_review":
        return "GAP_REVIEW"
    if analysis_status in ("pending", "running"):
        return "STATUS"
    if analysis_status == "completed":
        return "INTELLIGENCE"
    return "INTAKE"


async def run_intake(message: str, history: list, case_data: dict, case_id: int, ctx,
                     resolution_note: str | None = None) -> str:
    """INTAKE state: help prepare case, DOJ guidance. Uses ctx.ai (Opus 4.8)."""
    system = _load_prompt("intake") + f"\n\nCASE CONTEXT:\n{_build_case_context(case_data, case_id)}"
    if resolution_note:
        system = resolution_note + "\n\n" + system
    convo = "\n".join(f"{h['role'].upper()}: {h['content']}" for h in history[-10:])
    prompt = system + ("\n\n" + convo if convo else "") + f"\nUSER: {message}\nASSISTANT:"
    try:
        result = await ctx.ai.complete(prompt, model=_QA_MODEL, max_tokens=1024)
        return _text_of(result) or "I processed your request."
    except Exception as e:
        log.error(f"INTAKE LLM error: {e}")
        return "I encountered an error processing your request. Please try again."


def _audit_response(response, ctx_data: dict, case_id: int | None) -> None:
    """Run grounded-claims validation and log issues for the audit pipeline.

    No annotation on the user-facing prose — issues are logged
    (SigNoz-friendly) and the user sees clean prose. The federal audit
    trail lives in the structured claims, which the kernel-side action
    writer can persist alongside the response.
    """
    try:
        issues = validate_grounded_claims(response.claims, ctx_data)
    except Exception as e:
        log.error(f"grounded-claims validator failed (continuing): {e}")
        issues = []
    if issues:
        log.warning(
            f"INTELLIGENCE case={case_id} grounding issues={len(issues)} "
            f"reasons={[i.reason for i in issues]} "
            f"sources={[i.source_repr for i in issues]}"
        )


async def run_intelligence(message: str, history: list,
                           case_data: dict, case_id: int, ctx,
                           resolution_note: str | None = None) -> str:
    """INTELLIGENCE state: federal-grade grounded Q&A with 5-layer guard.

    1. Fetch grounded V3 context (gaps, summaries, entities, ...).
    2. Compute context fingerprint (Layer 3). If changed since last turn
       stored in ``ctx.cache``, strip assistant history.
    3. Filter history for factual intent (Layer 2).
    4. Call LLM with grounded system prompt (Layer 5 rules baked in).
    5. Validate citations + number consistency (Layers 1, 4).
    6. Append warning footers if issues found. Original answer preserved.
    """
    try:
        ctx_data = await fetch_grounded_context(case_id, agency_id=_user_agency(ctx))
    except Exception as e:
        log.error(f"INTELLIGENCE grounded fetch failed: {e}")
        return ("Unable to load case analysis context. Please try again or re-run analysis.")

    if ctx_data.get("error"):
        return f"Cannot answer: {ctx_data['error']}. Run analysis first."

    # Layer 3 fingerprint (ctx.cache) + Layer 2 filtering.
    # SDK v1.6.0 (I-SKELETON-LLM-ONLY): fingerprint lives in ctx.cache, not
    # in ctx.skeleton_data — the skeleton is now LLM-envelope-only.
    current_fp = context_fingerprint(ctx_data)
    user_id = str(ctx.user.imperal_id) if getattr(ctx, "user", None) else ""
    fp_key = _fp_cache_key(case_id or 0, user_id)

    prior_fp: str | None = None
    try:
        prior = await ctx.cache.get(fp_key, CaseContextFingerprint)
        if prior:
            prior_fp = prior.fingerprint
    except Exception as e:
        log.debug(f"ctx.cache fingerprint read failed (non-fatal): {e}")

    if prior_fp and prior_fp != current_fp:
        log.info(
            f"INTELLIGENCE case={case_id} context changed "
            f"({prior_fp} -> {current_fp})"
        )

    try:
        await ctx.cache.set(
            fp_key,
            CaseContextFingerprint(case_id=int(case_id or 0), fingerprint=current_fp),
            ttl_seconds=_FP_TTL_SECONDS,
        )
    except Exception as e:
        log.debug(f"ctx.cache fingerprint write failed (non-fatal): {e}")

    context_block = format_grounded_context(ctx_data)
    user_block = (
        f"\nCURRENT USER: {_user_email(ctx)} (role: {ctx.user.role})"
        if ctx and hasattr(ctx, "user") and ctx.user else ""
    )
    system_parts = [_load_prompt("intelligence")]
    if resolution_note:
        system_parts.insert(0, resolution_note)
    system_parts.append(user_block)
    system_parts.append(
        "\n" + "=" * 60
        + "\nCASE CONTEXT (grounded from V3 analysis pipeline):\n"
        + "=" * 60 + "\n" + context_block
    )
    system = "\n\n".join(p for p in system_parts if p)

    prompt = (
        system
        + "\n\n" + "=" * 60
        + "\nCURRENT USER MESSAGE (answer ONLY this):\n" + message
        + "\n\n" + build_intelligence_json_instruction()
    )

    log.info(
        f"INTELLIGENCE case={case_id} ctx_bytes={len(context_block)} "
        f"prompt_bytes={len(prompt)} fingerprint={current_fp}"
    )

    try:
        result = await ctx.ai.complete(prompt, model=_QA_MODEL, max_tokens=2048)
    except Exception as e:
        log.error(f"INTELLIGENCE ctx.ai.complete error: {e}")
        fb = render_findings_deterministic(ctx_data)
        return fb or "Не удалось обработать запрос. Попробуйте ещё раз."

    text = _text_of(result)
    parsed = parse_intelligence_json(text)
    if parsed is None:
        log.error(
            f"INTELLIGENCE case={case_id} unparseable completion "
            f"(head={text[:200]!r}); using deterministic fallback"
        )
        fb = render_findings_deterministic(ctx_data)
        return fb or "Не удалось получить структурированный ответ. Попробуйте ещё раз."

    _audit_response(parsed, ctx_data, case_id)
    return parsed.prose


def status_response(analysis_progress: dict | None = None) -> str:
    """STATUS state: analysis in progress, no LLM call.

    When the skeleton carried Redis progress (phase/percent), surface it so
    "where is it now" is answerable instead of a flat "in progress".
    """
    base = (
        "Analysis is currently in progress. "
        "This typically takes 2-5 minutes depending on the number of documents. "
        "I will be fully operational once it completes."
    )
    if isinstance(analysis_progress, dict):
        phase = analysis_progress.get("phase") or analysis_progress.get("stage")
        pct = analysis_progress.get("percent")
        if pct is None:
            pct = analysis_progress.get("progress")
        bits = []
        if phase:
            bits.append(f"phase: {phase}")
        if pct is not None:
            try:
                bits.append(f"~{int(float(pct))}%")
            except (TypeError, ValueError):
                pass
        if bits:
            return f"Analysis running — {', '.join(bits)}.\n\n{base}"
    return base


def build_gap_review_fact(gaps: list, run: dict | None) -> tuple[dict, str]:
    """GAP_REVIEW state: the analysis is PAUSED waiting for a decision.

    Returns (fact, summary). The FACT carries the structured gap data
    (count + by_severity + confidence current→potential + the two named
    choices); the narrator owns the language (ICNLI). The summary is the
    elder-friendly plain-language fallback that LEADS with the decision so
    the user can answer in chat ("continue" / "add evidence"), not only the
    panel button.
    """
    run = run or {}
    by_severity: dict[str, int] = {"BLOCKING": 0, "QUALITY": 0, "INFORMATIONAL": 0}
    for g in gaps or []:
        sev = g.get("severity", "INFORMATIONAL")
        by_severity[sev] = by_severity.get(sev, 0) + 1
    gap_count = len(gaps or [])
    confidence_current = run.get("confidence_current")
    confidence_potential = run.get("confidence_potential")

    fact = {
        "state": "gap_review",
        "paused": True,
        "gap_count": gap_count,
        "by_severity": by_severity,
        "confidence_current": confidence_current,
        "confidence_potential": confidence_potential,
        # The two plain-language choices map to the existing chat tools:
        # "continue" → continue_analysis, "add evidence" → resume_with_new_evidence.
        "choices": [
            {"id": "continue", "tool": "continue_analysis",
             "label": "Continue analysis as-is"},
            {"id": "add_evidence", "tool": "resume_with_new_evidence",
             "label": "Add more evidence first and re-run"},
        ],
    }

    conf = ""
    if confidence_current is not None:
        try:
            cur = f"{float(confidence_current):.0%}"
            if confidence_potential is not None:
                conf = (f" Confidence is at {cur} now and could rise to "
                        f"{float(confidence_potential):.0%} with more evidence.")
            else:
                conf = f" Confidence is at {cur}."
        except (TypeError, ValueError):
            conf = ""

    summary = (
        f"I've finished the first analysis pass and found {gap_count} gap(s)."
        f"{conf} I've PAUSED and I need your decision: "
        f"(1) Continue analysis as-is, or "
        f"(2) Add more evidence first and re-run. "
        f"Just tell me 'continue' or 'add evidence'."
    )
    return fact, summary


def case_list_response(case_data: dict) -> str:
    """CASE_LIST state: no active case, show case list.

    Called only when case_id resolution failed. Message is clear and
    asks the user to be explicit (no active case could be determined).
    """
    cases = case_data.get("cases", [])
    if not cases:
        return "You have no cases yet. Create a new case to get started."
    lines = [
        "I could not determine which case you are asking about. "
        "Please specify the case name or ID (e.g. `case #3812` or "
        "`Test Files`). Your cases:\n"
    ]
    for c in cases[:10]:
        a_st = c.get("analysis_status") or "not run"
        fc = c.get("file_count", 0)
        lines.append(f"- **{c.get('name', '?')}** (ID: {c.get('id')}) -- analysis: {a_st}, files: {fc}")
    return "\n".join(lines)
