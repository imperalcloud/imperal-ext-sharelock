"""
Sharelock v2 — Cases API client: analysis artifacts (runs/gaps/graph/etc.).

Rule-6 split of queries.py (2026-06-12): the V3 analysis read surface —
runs, gaps, graph, taxonomy, summaries, entities, inspections, audit log —
plus the gap-decision write. ``queries`` re-exports every function below,
so ALL existing ``queries.<fn>`` call sites (and test monkeypatching via
``queries.<fn>``) keep working unchanged.

Transport comes from the leaf ``queries_http`` module — never from
``queries`` — so there is no circular import under any import order.
"""
import logging
from typing import Optional

from queries_http import _get, _post

log = logging.getLogger("sharelock-v2.queries")


# ── Runs ──────────────────────────────────────────────────────────────────────


async def list_runs(case_id: int, agency_id: Optional[str] = None) -> list:
    """List all analysis runs for a case, newest version first."""
    resp = await _get(f"/cases/{case_id}/runs", agency_id=agency_id)
    return resp if isinstance(resp, list) else []


async def get_run(case_id: int, run_id: int, agency_id: Optional[str] = None) -> dict:
    """Fetch a single run."""
    return await _get(f"/cases/{case_id}/runs/{run_id}", agency_id=agency_id)


async def get_latest_active_run(case_id: int, agency_id: Optional[str] = None) -> dict:
    """Return the latest run for a case (or {} if none). Newest first by version."""
    runs = await list_runs(case_id, agency_id=agency_id)
    return runs[0] if runs else {}


# ── Gaps ──────────────────────────────────────────────────────────────────────


async def list_gaps(case_id: int, run_id: int | None = None,
                    agency_id: Optional[str] = None) -> list:
    """List gaps (BLOCKING→QUALITY→INFORMATIONAL)."""
    path = f"/cases/{case_id}/analysis/gaps"
    if run_id is not None:
        path += f"?run_id={run_id}"
    resp = await _get(path, agency_id=agency_id)
    return resp if isinstance(resp, list) else []


async def post_gap_decision(case_id: int, run_id: int, decision: str,
                            reasoning: str | None = None,
                            agency_id: Optional[str] = None) -> dict:
    """Signal gap decision: continue | cancel | add_evidence."""
    body: dict = {"run_id": run_id, "decision": decision}
    if reasoning:
        body["reasoning"] = reasoning
    return await _post(f"/cases/{case_id}/analysis/gaps/decision", body,
                       agency_id=agency_id)


# ── Graph / Taxonomy ──────────────────────────────────────────────────────────


async def get_graph(case_id: int, max_nodes: int = 200, min_mentions: int = 1,
                    agency_id: Optional[str] = None) -> dict:
    """Cytoscape graph for a case."""
    path = f"/cases/{case_id}/graph?max_nodes={max_nodes}&min_mentions={min_mentions}"
    resp = await _get(path, agency_id=agency_id)
    return resp if isinstance(resp, dict) else {}


async def get_taxonomy(case_id: int, agency_id: Optional[str] = None) -> list:
    """OSAC taxonomy rows: category, subcategory, file_count, total_size."""
    resp = await _get(f"/cases/{case_id}/taxonomy", agency_id=agency_id)
    return resp if isinstance(resp, list) else []


# ── Summaries (V3 grounded analysis) ──────────────────────────────────────────


async def list_summaries(case_id: int, run_id: int | None = None,
                         agency_id: Optional[str] = None) -> list:
    """List category summaries for a run."""
    path = f"/cases/{case_id}/summaries"
    if run_id is not None:
        path += f"?run_id={run_id}"
    resp = await _get(path, agency_id=agency_id)
    return resp if isinstance(resp, list) else []


# ── Entities ──────────────────────────────────────────────────────────────────


async def list_entities(case_id: int, limit: int = 50,
                        type_filter: str | None = None,
                        min_mentions: int = 0,
                        agency_id: Optional[str] = None) -> list:
    """List entities ordered by mention_count DESC."""
    params = [f"limit={limit}"]
    if type_filter:
        params.append(f"type={type_filter}")
    if min_mentions > 0:
        params.append(f"min_mentions={min_mentions}")
    path = f"/cases/{case_id}/entities?" + "&".join(params)
    resp = await _get(path, agency_id=agency_id)
    return resp if isinstance(resp, list) else []


# ── Inspections (V3 per-file forensic extraction) ─────────────────────────────


async def list_inspections(case_id: int, limit: int = 500, offset: int = 0,
                           category: str | None = None,
                           subcategory: str | None = None,
                           agency_id: Optional[str] = None) -> list:
    """List file_inspections rows for a case."""
    params = [f"limit={limit}", f"offset={offset}"]
    if category:
        params.append(f"category={category}")
    if subcategory:
        params.append(f"subcategory={subcategory}")
    path = f"/cases/{case_id}/inspections?" + "&".join(params)
    resp = await _get(path, agency_id=agency_id)
    return resp if isinstance(resp, list) else []


# ── Audit log ─────────────────────────────────────────────────────────────────


async def get_audit_log(case_id: int, limit: int = 50,
                        agency_id: Optional[str] = None) -> list:
    """Paginated audit events (chronological order, oldest first)."""
    resp = await _get(f"/cases/{case_id}/audit?limit={limit}", agency_id=agency_id)
    return resp if isinstance(resp, list) else []
