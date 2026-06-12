"""
Sharelock v2 — Skeleton refresh and proactive alerts (SDK v1.6.0).

Per I-SKELETON-LLM-ONLY: the skeleton carries scalar fields for the classifier
envelope + a compact active-case snapshot — panels and chat handlers read the
full per-render dict from ``ctx.cache`` (see ``cache_models.CaseSummary``).

The refresh tool continues to fetch the latest case roster so it can:
1. Surface ``cases_count`` / ``analysis_status`` for the classifier envelope.
2. Populate the compact active-case fields for envelope readability.

It does NOT call ``ctx.skeleton.update(...)`` — the kernel persists the
returned dict via the ``skeleton_save_section`` activity.

The paired ``skeleton_alert_case_status`` tool keeps custom Sharelock
alert copy (completed / running / error + new-file notifications). The
kernel invokes it when the refresh payload changes.

Note on active_case_id:
  The "active" case returned here is a *hint only*. It used to be
  ``cases[0]`` (arbitrary), which made case_chat rely on a wrong default
  and caused hallucinations. We now pick the most likely active case:
    running > pending > most-recently-analyzed > first.
  case_chat still performs per-message resolution on top of this hint.
"""
import logging
import os

from app import ext, _user_id, _user_agency
from auth_gate import locked_fact, unlock_ok
import queries

log = logging.getLogger("sharelock-v2.skeleton")


def _pick_active_case(enriched: list[dict]) -> dict | None:
    """Choose the most-likely-active case.

    Priority:
      1. analysis_status == "gap_review" (PAUSED — the user owes a decision;
         focus it so the panel/skeleton lead with the pending choice).
      2. analysis_status == "running"  (user is watching progress).
      3. analysis_status == "pending"  (user just kicked off analysis).
      4. Most recent analysis_updated_at (if present on the row).
      5. First case in the list (deterministic fallback).
    """
    if not enriched:
        return None
    gap_review = [c for c in enriched if c.get("analysis_status") == "gap_review"]
    if gap_review:
        return gap_review[0]
    running = [c for c in enriched if c.get("analysis_status") == "running"]
    if running:
        return running[0]
    pending = [c for c in enriched if c.get("analysis_status") == "pending"]
    if pending:
        return pending[0]
    with_ts = [c for c in enriched if c.get("analysis_updated_at")]
    if with_ts:
        with_ts.sort(key=lambda c: c.get("analysis_updated_at") or "", reverse=True)
        return with_ts[0]
    return enriched[0]


@ext.skeleton("case_status", ttl=300, alert=True,
              description="Sharelock case roster + active-case snapshot for classifier.")
async def on_skeleton_refresh(ctx, **kwargs):
    """Refresh ALL case data for the user. Gives AI full picture of workspace."""
    if not await unlock_ok(ctx):
        return {"response": locked_fact()}
    user_id = _user_id(ctx)
    agency = _user_agency(ctx)
    try:
        cases = await queries.get_cases(user_id, agency_id=agency)
        if not cases:
            return {"response": {"cases_count": 0, "cases": [], "active_case_id": None,
                                 "analysis_status": None}}

        enriched = []
        for c in cases[:10]:
            cid = c.get("id")
            entry = {"id": cid, "name": c.get("name", ""), "status": c.get("status", "")}
            try:
                ar = await queries.get_analysis(cid, agency_id=agency)
                entry["analysis_status"] = ar.get("analysis_status")
                entry["analysis_updated_at"] = ar.get("analysis_updated_at") or \
                                               ar.get("updated_at") or \
                                               ar.get("completed_at")
            except Exception:
                entry["analysis_status"] = None
            try:
                files = await queries.get_files(cid, agency_id=agency)
                entry["file_count"] = len(files)
            except Exception:
                entry["file_count"] = 0
            enriched.append(entry)

        active = _pick_active_case(enriched) or enriched[0]
        case_id = active.get("id")
        case_name = active.get("name", f"Case-{case_id}")
        analysis_status = active.get("analysis_status")

        files = []
        file_count = 0
        try:
            files_raw = await queries.get_files(case_id, agency_id=agency)
            file_count = len(files_raw)
            files = [{"filename": f.get("filename", ""), "size": f.get("size", 0)}
                     for f in files_raw]
        except Exception:
            pass

        # Read analysis progress from Redis (V2 pipeline)
        analysis_progress = None
        try:
            import json as _json
            import redis as _redis_mod
            _redis_url = os.environ.get("REDIS_URL")
            if not _redis_url:
                raise RuntimeError(
                    "REDIS_URL env var is required (no plaintext credential fallback)"
                )
            _r = _redis_mod.from_url(_redis_url, decode_responses=True)
            progress_key = f"sharelock:analysis:{case_id}:progress"
            progress_raw = _r.get(progress_key)
            if progress_raw:
                analysis_progress = _json.loads(progress_raw)
        except Exception:
            pass

        return {"response": {
            "cases": enriched,
            "cases_count": len(cases),
            "active_case_id": case_id,
            "case_name": case_name,
            "analysis_status": analysis_status,
            "file_count": file_count,
            "files": files,
            "analysis_progress": analysis_progress,
        }}
    except Exception as e:
        log.warning(f"skeleton refresh failed: {e}")
        return {"response": {"error": str(e)}}


async def _safe_notify(ctx, message: str, **kwargs) -> None:
    """Fire ``ctx.notify`` for a proactive push, never breaking the skeleton.

    DOJ users barely use computers — a paused analysis must PING them, not
    hide behind a panel badge. ``ctx.notify`` may be absent (older Context,
    test harness) or fail (gateway down); either way we swallow and log so a
    notify problem can never break the skeleton-alert path.
    """
    notify = getattr(ctx, "notify", None)
    if notify is None:
        return
    try:
        await notify(message, priority="high", channel="in_app")
    except Exception as e:  # noqa: BLE001 — notify is best-effort
        log.warning(f"ctx.notify failed (continuing): {e}")


@ext.tool("skeleton_alert_case_status", description="Skeleton alert")
async def on_skeleton_alert(ctx, **kwargs):
    """Proactive alert when case status or files change.

    Fires ONLY on a status TRANSITION (old != new) so a paused/completed
    case is announced once, not on every poll. The gap_review arm also
    PUSHES a notification (ctx.notify) — the proactive ping a barely-
    technical user needs when the analysis is waiting on their decision.
    """
    old = kwargs.get("old", {})
    new = kwargs.get("new", {})
    case_name = new.get("case_name", "your case")

    old_status = old.get("analysis_status")
    new_status = new.get("analysis_status")

    if old_status != new_status:
        if new_status == "gap_review":
            # Surface the pending decision AND push a notification — never
            # leave the needed choice hidden behind a silent panel button.
            await _safe_notify(
                ctx,
                f"Sharelock paused on case {case_name} — it needs your "
                f"decision (continue or add evidence).",
            )
            return (f"Analysis of case **{case_name}** is PAUSED — it needs "
                    f"your decision: continue, or add more evidence. "
                    f"Just tell me 'continue' or 'add evidence'.")
        if new_status == "completed":
            return f"Analysis of case **{case_name}** is complete. You can now ask questions about the findings."
        elif new_status == "running":
            return f"Analysis of case **{case_name}** has started. This typically takes 2-5 minutes."
        elif new_status == "error":
            return f"Analysis of case **{case_name}** failed. Please try again."

    old_files = old.get("file_count", 0)
    new_files = new.get("file_count", 0)
    if new_files > old_files and old_files > 0:
        diff = new_files - old_files
        return f"{diff} new file(s) uploaded to case **{case_name}**. Total: {new_files} files."

    return ""
