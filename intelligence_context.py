"""
Sharelock v2 — Grounded INTELLIGENCE context fetcher.

Fetches V3 analysis artifacts from Cases API in parallel and returns a
structured dict. Pure I/O orchestration -- rendering lives in
intelligence_format.py.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

import queries

log = logging.getLogger("sharelock-v2.intelligence_context")

_MAX_ENTITIES = 30
_MAX_AUDIT_EVENTS = 15
_INSPECTIONS_PAGE = 1000
_INSPECTIONS_HARD_LIMIT = 5000  # safety bound for very large cases


async def _fetch_inspection_counts(case_id: int) -> dict:
    """Aggregate per-category inspection counts.

    Returns dict keyed by "category/subcategory" (or "category" when subcategory
    is empty) with counts: total, inspected_complete, hash_failed, with_text,
    with_entities, importance_avg.

    A file is considered:
      - total:               row exists for (case_id, sha256)
      - inspected_complete:  inspection_complete == 1
      - hash_failed:         is_duplicate_of_sha not null (soft-dup or hash collision)
      - with_text:           extracted_text_sample non-empty
      - with_entities:       primary_entities non-empty
      - importance_avg:      mean(importance_score) across rows where set

    Also returns top-level aggregate row under key "__TOTAL__".
    On error: returns {"__ERROR__": "<msg>"}.
    """
    offset = 0
    rows: list[dict] = []
    try:
        while offset < _INSPECTIONS_HARD_LIMIT:
            page = await queries.list_inspections(
                case_id, limit=_INSPECTIONS_PAGE, offset=offset,
            )
            if not isinstance(page, list):
                break
            rows.extend(page)
            if len(page) < _INSPECTIONS_PAGE:
                break
            offset += _INSPECTIONS_PAGE
    except Exception as e:
        log.error(f"_fetch_inspection_counts({case_id}) failed: {e}")
        return {"__ERROR__": str(e)}

    buckets: dict[str, dict] = defaultdict(lambda: {
        "total": 0, "inspected_complete": 0, "hash_failed": 0,
        "with_text": 0, "with_entities": 0,
        "_imp_sum": 0.0, "_imp_n": 0,
    })
    total_all = {
        "total": 0, "inspected_complete": 0, "hash_failed": 0,
        "with_text": 0, "with_entities": 0,
        "_imp_sum": 0.0, "_imp_n": 0,
    }

    for r in rows:
        cat = r.get("category") or "unknown"
        sub = r.get("subcategory") or ""
        key = f"{cat}/{sub}" if sub else cat
        b = buckets[key]
        for target in (b, total_all):
            target["total"] += 1
            if r.get("inspection_complete"):
                target["inspected_complete"] += 1
            if r.get("is_duplicate_of_sha"):
                target["hash_failed"] += 1
            if r.get("extracted_text_sample"):
                target["with_text"] += 1
            pe = r.get("primary_entities")
            if pe and isinstance(pe, list) and len(pe) > 0:
                target["with_entities"] += 1
            imp = r.get("importance_score")
            if isinstance(imp, (int, float)):
                target["_imp_sum"] += float(imp)
                target["_imp_n"] += 1

    def _finalize(b: dict) -> dict:
        out = {k: v for k, v in b.items() if not k.startswith("_")}
        out["importance_avg"] = (
            b["_imp_sum"] / b["_imp_n"] if b["_imp_n"] > 0 else None
        )
        return out

    result = {k: _finalize(v) for k, v in buckets.items()}
    result["__TOTAL__"] = _finalize(total_all)
    return result


async def fetch_grounded_context(case_id: int) -> dict:
    """Fetch V3 grounded data from Cases API in parallel.

    Returns dict with all artifacts for the latest completed run.
    On error: returns {"error": "<message>"}.
    """
    try:
        case = await queries.get_case(case_id)
    except Exception as e:
        log.error(f"get_case({case_id}) failed: {e}")
        return {"error": f"Failed to load case: {e}"}

    if not case or not case.get("id"):
        return {"error": "Case not found"}

    run_id = case.get("active_run_id")
    if not run_id:
        return {"error": "No analysis run available for this case"}

    try:
        run = await queries.get_run(case_id, run_id)
    except Exception as e:
        log.error(f"get_run({case_id}, {run_id}) failed: {e}")
        run = {"run_id": run_id}

    (gaps, summaries, entities, graph, taxonomy, audit, runs_history,
     inspections) = await asyncio.gather(
        queries.list_gaps(case_id, run_id),
        queries.list_summaries(case_id, run_id),
        queries.list_entities(case_id, limit=_MAX_ENTITIES),
        queries.get_graph(case_id, max_nodes=30, min_mentions=2),
        queries.get_taxonomy(case_id),
        queries.get_audit_log(case_id, limit=_MAX_AUDIT_EVENTS),
        queries.list_runs(case_id),
        _fetch_inspection_counts(case_id),
        return_exceptions=True,
    )

    def _ok(x, default):
        return x if not isinstance(x, BaseException) else default

    summaries_safe = _ok(summaries, [])
    gaps_safe = _ok(gaps, [])
    runs_history_safe = _ok(runs_history, [])

    # Carry-forward summaries + gaps from the most recent prior completed
    # run when V5 incremental skipped Phase 4.5/5/6 on the active run.
    # The active run row has no `_cross_cutting` / `_indictment` /
    # per-category summaries, so the LLM gets only entity-graph metadata
    # and answers "no information about case subject". This block walks
    # runs_history (newest-first, skipping current run) and pulls
    # summaries/gaps from the first prior completed run that has them.
    # Federal-grade: the `case_theory`, `narrative_synthesis`, target
    # subjects and candidate charges live in `_indictment` /
    # `_cross_cutting` rows and are the actual analytical truth — they
    # do not become invalid because the user clicked "Re-analyze" on a
    # case with no file changes.
    carried_summaries_run = None
    carried_gaps_run = None
    if not summaries_safe or not gaps_safe:
        completed_prior = [
            r for r in (runs_history_safe or [])
            if isinstance(r, dict)
               and r.get("status") == "completed"
               and r.get("run_id") is not None
               and int(r.get("run_id")) != int(run_id)
        ]
        completed_prior.sort(key=lambda r: int(r.get("run_id")), reverse=True)
        for prev in completed_prior:
            prev_rid = int(prev.get("run_id"))
            if not summaries_safe and carried_summaries_run is None:
                try:
                    s = await queries.list_summaries(case_id, prev_rid)
                    if s:
                        summaries_safe = s
                        carried_summaries_run = prev_rid
                except Exception as e:
                    log.debug(f"carry-forward summaries from run {prev_rid} failed: {e}")
            if not gaps_safe and carried_gaps_run is None:
                try:
                    g = await queries.list_gaps(case_id, prev_rid)
                    if g:
                        gaps_safe = g
                        carried_gaps_run = prev_rid
                except Exception as e:
                    log.debug(f"carry-forward gaps from run {prev_rid} failed: {e}")
            if summaries_safe and gaps_safe:
                break
        if carried_summaries_run or carried_gaps_run:
            log.info(
                f"fetch_grounded_context: carry-forward case={case_id} "
                f"active={run_id} summaries_from={carried_summaries_run} "
                f"gaps_from={carried_gaps_run}"
            )

    return {
        "case": case,
        "run": run,
        "runs_history": runs_history_safe,
        "gaps": gaps_safe,
        "summaries": summaries_safe,
        "entities": _ok(entities, []),
        "graph_stats": (_ok(graph, {}) or {}).get("stats") or {},
        "taxonomy": _ok(taxonomy, []),
        "audit": _ok(audit, []),
        "inspections": _ok(inspections, {}),
        "_carried_summaries_run": carried_summaries_run,
        "_carried_gaps_run": carried_gaps_run,
    }
