"""
Sharelock v2 — INTELLIGENCE context formatter.

Renders V3 analysis artifacts with numbered source IDs (G1, S1, I2:T1,
CC1, E1, A1, ...) so the LLM can cite sources inline under the federal
"no hallucination" protocol.

Pure rendering module -- no I/O, no LLM calls.
"""
from __future__ import annotations

# Limits for rendered context (keep system prompt bounded)
_MAX_GAP_DESC = 300
_MAX_CAT_SUMMARY_TEXT = 600
_MAX_CC_NARRATIVE = 800
_MAX_CC_FINDINGS = 5
_MAX_CC_FINDING_DESC = 240
_MAX_INDICT_THEORY = 500
_MAX_INDICT_EVIDENCE = 200
_MAX_INDICT_MERIT_REASONING = 300
_MAX_ENTITIES = 30
_MAX_TAX_ROWS = 30
_MAX_HISTORY_RUNS = 8
_MAX_AUDIT_EVENTS = 15


def _fmt_case_metadata(case: dict, lines: list) -> None:
    lines.append("=== CASE METADATA ===")
    lines.append(f"[CASE:id] case_id: {case.get('id')}")
    lines.append(f"[CASE:name] case_name: \"{case.get('name', '')}\"")
    lines.append(f"[CASE:status] analysis_status: {case.get('analysis_status')}")
    lines.append(f"[CASE:version] analysis_version: v{case.get('analysis_version', '?')}")
    lines.append(f"[CASE:active_run_id] active_run_id: {case.get('active_run_id')}")
    lines.append(f"[CASE:confidence_current] {case.get('confidence_score_current')}")
    lines.append(f"[CASE:confidence_potential] {case.get('confidence_score_potential')}")
    lines.append(f"[CASE:created_at] {case.get('created_at')}")


def _fmt_run(run: dict, lines: list) -> None:
    lines.append(f"\n=== RUN INFO (run {run.get('run_id')}, v{run.get('version')}) ===")
    lines.append(f"[RUN:files_total] files_total: {run.get('files_total')}")
    lines.append(f"[RUN:started_at] started_at: {run.get('started_at')}")
    lines.append(f"[RUN:completed_at] completed_at: {run.get('completed_at')}")
    lines.append(f"[RUN:status] status: {run.get('status')}")
    if run.get("confidence_current") is not None:
        lines.append(f"[RUN:confidence_current] {run.get('confidence_current')}")
    if run.get("confidence_potential") is not None:
        lines.append(f"[RUN:confidence_potential] {run.get('confidence_potential')}")


def _fmt_taxonomy(taxonomy: list, lines: list) -> None:
    if not taxonomy:
        return
    lines.append("\n=== TAXONOMY (OSAC category file counts) ===")
    total_files = 0
    for t in taxonomy[:_MAX_TAX_ROWS]:
        cat = t.get("category", "?")
        sub = t.get("subcategory", "") or ""
        fc = t.get("file_count", 0) or 0
        total_files += int(fc)
        size = int(t.get("total_size") or 0)
        size_kb = size // 1024 if size else 0
        label = f"{cat}/{sub}" if sub else cat
        tag = f"[TAX:{cat}:{sub}]" if sub else f"[TAX:{cat}]"
        lines.append(f"{tag} {label}: {fc} files ({size_kb} KB)")
    lines.append(f"[TAX:total_file_count_sum] Sum across taxonomy rows: {total_files}")


def _fmt_gaps(gaps: list, lines: list) -> None:
    if not gaps:
        lines.append("\n=== GAPS (0 total) ===")
        lines.append("No gaps flagged for this run.")
        return
    blocking = sum(1 for g in gaps if g.get("severity") == "BLOCKING")
    quality = sum(1 for g in gaps if g.get("severity") == "QUALITY")
    info = sum(1 for g in gaps if g.get("severity") == "INFORMATIONAL")
    lines.append(
        f"\n=== GAPS ({len(gaps)} total: {blocking} BLOCKING, "
        f"{quality} QUALITY, {info} INFORMATIONAL) ==="
    )
    for i, g in enumerate(gaps, 1):
        desc = (g.get("description") or "").strip().replace("\n", " ")
        desc = desc[:_MAX_GAP_DESC]
        cites = ", ".join(g.get("standard_citations") or [])
        impact = g.get("impact_on_confidence")
        impact_s = f"{impact:+.2f}" if isinstance(impact, (int, float)) else (impact or "?")
        lines.append(
            f"[G{i}] {g.get('severity')} {g.get('gap_type')}: {desc}\n"
            f"     Citations: {cites or '(none)'}\n"
            f"     Impact on confidence: {impact_s}"
        )


def _fmt_category_summaries(cat_summs: list, lines: list) -> None:
    if not cat_summs:
        return
    lines.append(f"\n=== CATEGORY SUMMARIES ({len(cat_summs)}) ===")
    for i, s in enumerate(cat_summs, 1):
        sj = s.get("summary_json") or {}
        exec_sum = sj.get("executive_summary") or ""
        if not exec_sum:
            exec_sum = str(sj)[:_MAX_CAT_SUMMARY_TEXT]
        exec_sum = (exec_sum or "").replace("\n", " ")[:_MAX_CAT_SUMMARY_TEXT]
        conf = s.get("confidence_level") or "?"
        cat = s.get("category", "?")
        lines.append(f"[S{i}] {cat} (confidence: {conf}): {exec_sum}")
        findings = sj.get("key_findings") or []
        if findings and isinstance(findings, list):
            for j, f in enumerate(findings[:3], 1):
                f_str = str(f) if not isinstance(f, dict) else (
                    f.get("finding") or f.get("title") or f.get("description") or str(f)
                )
                lines.append(f"  [S{i}:F{j}] {f_str[:200]}")


def _fmt_cross_cutting(cross_cut: dict | None, lines: list) -> None:
    if not cross_cut:
        return
    sj = cross_cut.get("summary_json") or {}
    lines.append("\n=== CROSS-CUTTING DEEP ANALYSIS (Opus Phase 5) ===")
    narrative = (sj.get("narrative_synthesis") or "").replace("\n", " ")[:_MAX_CC_NARRATIVE]
    lines.append(f"[CC1] Narrative synthesis: {narrative}")
    findings = sj.get("cross_cutting_findings") or []
    for i, f in enumerate(findings[:_MAX_CC_FINDINGS], 1):
        title = f.get("title") or f.get("finding") or "?"
        desc = (f.get("description") or "").replace("\n", " ")[:_MAX_CC_FINDING_DESC]
        lines.append(f"[CC2:F{i}] {title}: {desc}")
    contras = sj.get("contradictions_found") or []
    if contras:
        lines.append(f"[CC3] Contradictions found ({len(contras)}):")
        for i, c in enumerate(contras[:3], 1):
            c_str = str(c) if not isinstance(c, dict) else (
                c.get("description") or c.get("finding") or str(c)
            )
            lines.append(f"  [CC3:C{i}] {c_str[:200]}")
    lines.append(f"[CC4] Confidence: {sj.get('confidence_assessment', '?')}")


def _fmt_indictment(indict: dict | None, lines: list) -> None:
    if not indict:
        return
    sj = indict.get("summary_json") or {}
    lines.append("\n=== INDICTMENT (Federal Prosecution Analysis, Opus Phase 6) ===")
    theory = (sj.get("case_theory") or "").replace("\n", " ")[:_MAX_INDICT_THEORY]
    lines.append(f"[I1] Case theory: {theory}")
    targets = sj.get("target_subjects") or []
    if targets:
        lines.append(f"[I2] Target subjects ({len(targets)}):")
        for i, t in enumerate(targets, 1):
            name = t.get("name") or "?"
            role = t.get("role") or "?"
            ev = (t.get("evidence_summary") or "").replace("\n", " ")[:_MAX_INDICT_EVIDENCE]
            lines.append(f"  [I2:T{i}] {name} ({role}): {ev}")
    charges = sj.get("candidate_charges") or []
    if charges:
        lines.append(f"[I3] Candidate charges ({len(charges)}):")
        for i, c in enumerate(charges, 1):
            code = c.get("charge_code") or "?"
            title = c.get("charge_title") or "?"
            tgt = c.get("target_subject") or "?"
            strength = c.get("overall_strength") or "?"
            lines.append(
                f"  [I3:C{i}] {code} {title} (target: {tgt}, strength: {strength})"
            )
    brady = sj.get("brady_giglio_flags") or []
    if brady:
        types = [b.get("type") if isinstance(b, dict) else str(b) for b in brady[:5]]
        lines.append(f"[I4] Brady/Giglio flags ({len(brady)}): {', '.join(str(t) for t in types)}")
    merit = (sj.get("prosecutive_merit_overall") or "?").upper()
    reasoning = (sj.get("prosecutive_merit_reasoning") or "").replace(
        "\n", " ")[:_MAX_INDICT_MERIT_REASONING]
    lines.append(f"[I5] Prosecutive merit: {merit} -- {reasoning}")


def _fmt_entities(entities: list, lines: list) -> None:
    if not entities:
        return
    lines.append(f"\n=== TOP ENTITIES ({len(entities)}) ===")
    for i, e in enumerate(entities[:_MAX_ENTITIES], 1):
        t = e.get("type", "?")
        v = str(e.get("value") or e.get("normalized_value") or "?")[:80]
        mc = e.get("mention_count", 0)
        lines.append(f"[E{i}] {t} \"{v}\" (mentions: {mc})")


def _fmt_graph(stats: dict, lines: list) -> None:
    if not stats:
        return
    lines.append("\n=== GRAPH STATS ===")
    lines.append(f"[GRAPH:total_entities] {stats.get('total_entities', '?')}")
    lines.append(f"[GRAPH:total_edges] {stats.get('total_edges', '?')}")
    lines.append(f"[GRAPH:orphan_count] {stats.get('orphan_count', '?')}")
    if stats.get("total_entities_considered") is not None:
        lines.append(f"[GRAPH:total_considered] {stats.get('total_entities_considered')}")


def _fmt_run_history(history: list, lines: list) -> None:
    if not history:
        return
    lines.append(f"\n=== RUN HISTORY ({len(history)} runs) ===")
    for r in history[:_MAX_HISTORY_RUNS]:
        lines.append(
            f"- Run {r.get('run_id')} v{r.get('version')}: {r.get('status')} "
            f"(files: {r.get('files_total')}, completed: {r.get('completed_at') or '-'})"
        )


def _fmt_audit(audit: list, lines: list) -> None:
    if not audit:
        return
    recent = audit[-_MAX_AUDIT_EVENTS:] if len(audit) > _MAX_AUDIT_EVENTS else audit
    lines.append(f"\n=== RECENT AUDIT EVENTS (last {len(recent)}) ===")
    for i, a in enumerate(recent, 1):
        ts = (a.get("occurred_at") or "")[:19]
        lines.append(f"[A{i}] {ts} {a.get('event_type')} by {a.get('actor')}")



def _fmt_inspection_status(inspections: dict, lines: list) -> None:
    """Render per-category inspection counts (INS:*) — distinct from TAX:* file counts.

    TAX:* = files observed in the census (uploaded / present in storage).
    INS:* = files that went through per-file forensic extraction.
    A category SUMMARY failure does not reduce inspected_complete — these are
    DIFFERENT pipeline stages. Rule 7 of the federal protocol hinges on this.
    """
    if not inspections or inspections.get("__ERROR__"):
        if inspections and inspections.get("__ERROR__"):
            lines.append(
                "\n=== INSPECTION STATUS PER CATEGORY ===\n"
                f"[INS:error] {inspections['__ERROR__']}"
            )
        return
    total = inspections.get("__TOTAL__") or {}
    lines.append("\n=== INSPECTION STATUS PER CATEGORY ===")
    # Order stable: alphabetical by category key
    for key in sorted(k for k in inspections.keys() if k not in ("__TOTAL__", "__FILES__")):
        b = inspections[key]
        imp = b.get("importance_avg")
        imp_s = f"{imp:.2f}" if isinstance(imp, (int, float)) else "—"
        lines.append(
            f"[INS:{key}] total={b['total']}, "
            f"inspected_complete={b['inspected_complete']}, "
            f"hash_failed={b['hash_failed']}, "
            f"with_text={b['with_text']}, "
            f"with_entities={b['with_entities']} "
            f"(avg importance: {imp_s})"
        )
    t_imp = total.get("importance_avg")
    t_imp_s = f"{t_imp:.2f}" if isinstance(t_imp, (int, float)) else "—"
    lines.append(
        f"[INS:__TOTAL__] total={total.get('total', 0)}, "
        f"inspected_complete={total.get('inspected_complete', 0)}, "
        f"hash_failed={total.get('hash_failed', 0)}, "
        f"with_text={total.get('with_text', 0)}, "
        f"with_entities={total.get('with_entities', 0)} "
        f"(avg importance: {t_imp_s})"
    )
    lines.append(
        "[INS:DEFINITION] total=rows in file_inspections; "
        "inspected_complete=inspection_complete=1 (per-file forensic extraction finished); "
        "hash_failed=is_duplicate_of_sha set; "
        "with_text=extracted_text_sample non-empty; "
        "with_entities=primary_entities non-empty. "
        "A category SUMMARY failure (see [G*] gaps) does NOT reduce inspected_complete."
    )


def _fmt_inspection_files(inspections: dict, lines: list) -> None:
    """Render bounded per-file detail (INS files) for file-level conversation."""
    files = (inspections or {}).get("__FILES__") or []
    if not files:
        return
    lines.append(f"\n=== TOP FILES BY IMPORTANCE ({len(files)}) ===")
    for i, f in enumerate(files, 1):
        path = f.get("latest_path") or "?"
        name = path.rsplit("/", 1)[-1]
        cat = f.get("category") or "?"
        sub = f.get("subcategory") or ""
        label = f"{cat}/{sub}" if sub else cat
        imp = f.get("importance_score")
        imp_s = f"{imp:.2f}" if isinstance(imp, (int, float)) else "—"
        purpose = (f.get("file_purpose") or "").replace("\n", " ")[:160]
        ents = ", ".join([str(e) for e in (f.get("primary_entities") or [])][:8])
        sample = (f.get("extracted_text_sample") or "").replace("\n", " ")[:200]
        lines.append(f"[F{i}] {name} ({label}, importance: {imp_s}) — {purpose}")
        if ents:
            lines.append(f"     entities: {ents}")
        if sample:
            lines.append(f"     sample: {sample}")


def format_grounded_context(ctx: dict) -> str:
    """Render grounded V3 context with numbered source IDs.

    Every fact has an ID that the LLM can cite inline.
    """
    if ctx.get("error"):
        return f"CONTEXT ERROR: {ctx['error']}"

    lines: list[str] = []
    case = ctx.get("case") or {}
    run = ctx.get("run") or {}

    _fmt_case_metadata(case, lines)
    _fmt_run(run, lines)
    _fmt_taxonomy(ctx.get("taxonomy") or [], lines)
    _fmt_inspection_status(ctx.get("inspections") or {}, lines)
    _fmt_inspection_files(ctx.get("inspections") or {}, lines)
    _fmt_gaps(ctx.get("gaps") or [], lines)

    summs = ctx.get("summaries") or []
    cross_cut = next((s for s in summs if s.get("category") == "_cross_cutting"), None)
    indictment = next((s for s in summs if s.get("category") == "_indictment"), None)
    cat_summs = [s for s in summs if not (s.get("category") or "").startswith("_")]

    _fmt_category_summaries(cat_summs, lines)
    _fmt_cross_cutting(cross_cut, lines)
    _fmt_indictment(indictment, lines)
    _fmt_entities(ctx.get("entities") or [], lines)
    _fmt_graph(ctx.get("graph_stats") or {}, lines)
    _fmt_run_history(ctx.get("runs_history") or [], lines)
    _fmt_audit(ctx.get("audit") or [], lines)

    return "\n".join(lines)


def render_findings_deterministic(ctx: dict) -> str:
    """Pure data->prose render of precomputed findings (no LLM).

    Safety net: surfaced when the LLM call fails / returns unparseable output,
    so a completed case never yields an empty answer. Labels are minimal and
    neutral; the primary Opus path owns user-facing language (ICNLI).
    """
    if not ctx or ctx.get("error"):
        return ""
    summs = ctx.get("summaries") or []
    indictment = next((s for s in summs if s.get("category") == "_indictment"), None)
    cross_cut = next((s for s in summs if s.get("category") == "_cross_cutting"), None)
    cat_summs = [s for s in summs if not (s.get("category") or "").startswith("_")]
    out: list[str] = []

    if indictment:
        sj = indictment.get("summary_json") or {}
        theory = (sj.get("case_theory") or "").strip()
        if theory:
            out.append(f"Case theory: {theory}")
        targets = sj.get("target_subjects") or []
        if targets:
            out.append("\nSubjects:")
            for t in targets:
                name = t.get("name") or "?"
                role = t.get("role") or ""
                ev = (t.get("evidence_summary") or "").strip()
                line = f"- {name}" + (f" ({role})" if role else "") + (f" — {ev}" if ev else "")
                out.append(line)
        charges = sj.get("candidate_charges") or []
        if charges:
            out.append("\nCandidate charges:")
            for c in charges:
                code = (c.get("charge_code") or "").strip()
                title = c.get("charge_title") or "?"
                tgt = c.get("target_subject") or ""
                head = f"- {code} {title}".strip() if code else f"- {title}"
                out.append(head + (f" — {tgt}" if tgt else ""))
        merit = (sj.get("prosecutive_merit_overall") or "").strip()
        if merit:
            reasoning = (sj.get("prosecutive_merit_reasoning") or "").strip()
            out.append(f"\nProsecutive merit: {merit}" + (f" — {reasoning}" if reasoning else ""))

    if cross_cut:
        narr = ((cross_cut.get("summary_json") or {}).get("narrative_synthesis") or "").strip()
        if narr:
            out.append(f"\nCross-cutting: {narr}")

    if cat_summs:
        out.append("\nBy category:")
        for s in cat_summs:
            cat = s.get("category", "?")
            es = ((s.get("summary_json") or {}).get("executive_summary") or "").strip()
            if es:
                out.append(f"- {cat}: {es}")

    return "\n".join(out).strip()
