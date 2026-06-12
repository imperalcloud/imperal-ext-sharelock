"""
Sharelock v2 — Track D4 read drill-down handlers (the long tail).

Read tools that expose the rest of the Cases API surface to Webbee: case files,
case detail, analysis status, the intelligence graph (summarised, NOT a
2655-node dump), entities (list + single), relationships, timeline events,
taxonomy, the audit / chain-of-custody log, and analysis runs.

Every list tool caps at <=50 items and carries native sdl.EntityList
``total``/``has_more`` (skeleton-projection lesson — never blow the cache /
Temporal payload envelope on a big case). A single ``_entity_list`` helper turns
raw Cases API rows into a capped EntityList payload so each handler stays small
and uniform. All tools are @require_unlock gated, action_type='read', declare
data_model= (V23), and thread agency_id = _user_agency(ctx).
"""
import logging

from pydantic import BaseModel, Field

from app import chat, _user_agency
from auth_gate import require_unlock
from imperal_sdk.chat import ActionResult
import queries
from models import (
    CaseFileListResponse, CaseDetail, AnalysisStatus, IntelligenceGraphSummary,
    EntityRecord, EntityListResponse,
)

log = logging.getLogger("sharelock-v2.handlers_drilldown")

_LIST_CAP = 50


# ── Parameter Models ──────────────────────────────────────────────────────────


class CaseIdParams(BaseModel):
    case_id: int = Field(..., description="Case ID")


class ListEntitiesParams(BaseModel):
    case_id: int = Field(..., description="Case ID")
    type_filter: str = Field("", description=(
        "Optional entity type to filter by (e.g. PERSON, ORG, ACCOUNT). Pass "
        "VERBATIM. Empty = all types."
    ))
    min_mentions: int = Field(0, description="Only entities with at least this many mentions")


class GetEntityParams(BaseModel):
    case_id: int = Field(..., description="Case ID")
    entity_id: int = Field(..., description="Entity ID to fetch")


# ── Helpers ─────────────────────────────────────────────────────────────────────


def _entity_list(rows: list, case_id: int, cap: int = _LIST_CAP) -> dict:
    """Turn raw Cases API rows into a capped sdl.EntityList payload.

    Returns the ``data`` dict the read handlers hand to
    ``ActionResult.success(data=...)``: capped ``items`` + native EntityList
    ``total``/``has_more`` so a 2655-row case never blows the cache/envelope.
    """
    rows = rows if isinstance(rows, list) else []
    total = len(rows)
    items = rows[:cap]
    return {"items": items, "total": total, "has_more": total > len(items),
            "case_id": case_id}


# ── Case files / detail / analysis status ───────────────────────────────────────


@chat.function("list_case_files", action_type="read",
               data_model=CaseFileListResponse,
               description="List the evidence files attached to a case")
@require_unlock
async def fn_list_case_files(ctx, params: CaseIdParams) -> ActionResult:
    agency = _user_agency(ctx)
    try:
        rows = await queries.get_files(params.case_id, agency_id=agency)
        data = _entity_list(rows, params.case_id)
        return ActionResult.success(
            data=data,
            summary=(f"{data['total']} file(s) in case {params.case_id}"
                     + (f" (showing {len(data['items'])})" if data["has_more"] else "")
                     + "."),
        )
    except Exception as e:
        return ActionResult.error(f"Failed to list files: {e}")


@chat.function("get_case_detail", action_type="read",
               data_model=CaseDetail,
               description="Get a case's details: status, analysis status, file count, created date")
@require_unlock
async def fn_get_case_detail(ctx, params: CaseIdParams) -> ActionResult:
    agency = _user_agency(ctx)
    try:
        case = await queries.get_case(params.case_id, agency_id=agency)
        if not case or not case.get("id"):
            return ActionResult.error(f"Case {params.case_id} not found.",
                                      retryable=False)
        return ActionResult.success(
            data={
                "id": case.get("id"),
                "name": case.get("name"),
                "status": case.get("status"),
                "analysis_status": case.get("analysis_status"),
                "file_count": case.get("file_count"),
                "active_run_id": case.get("active_run_id"),
                "created_at": case.get("created_at"),
            },
            summary=(f"**{case.get('name')}** (ID: {case.get('id')}) — "
                     f"{case.get('analysis_status') or case.get('status') or 'not run'}, "
                     f"{case.get('file_count', 0)} file(s)."),
        )
    except Exception as e:
        return ActionResult.error(f"Failed to load case: {e}")


@chat.function("analysis_status", action_type="read",
               data_model=AnalysisStatus,
               description="Get the current analysis status of a case (status, active run, version)")
@require_unlock
async def fn_analysis_status(ctx, params: CaseIdParams) -> ActionResult:
    agency = _user_agency(ctx)
    try:
        a = await queries.get_analysis(params.case_id, agency_id=agency) or {}
        return ActionResult.success(
            data={
                "case_id": params.case_id,
                "status": a.get("status"),
                "analysis_status": a.get("analysis_status") or a.get("status"),
                "active_run_id": a.get("active_run_id"),
                "version": a.get("version"),
            },
            summary=(f"Case {params.case_id} analysis: "
                     f"{a.get('analysis_status') or a.get('status') or 'not run'}."),
        )
    except Exception as e:
        return ActionResult.error(f"Failed to get analysis status: {e}")


# ── Intelligence graph (summarised) ─────────────────────────────────────────────


@chat.function("get_intelligence_graph", action_type="read",
               data_model=IntelligenceGraphSummary,
               description=(
                   "Summarise a case's intelligence graph: entity / relationship "
                   "counts plus the top entities by mention. Does not dump the "
                   "full graph — use list_entities / list_relationships to drill in."
               ))
@require_unlock
async def fn_get_intelligence_graph(ctx, params: CaseIdParams) -> ActionResult:
    """Summarise the graph — count nodes/edges + cap the top entities.
    NEVER dump the full node set (a forensic case can hold 2655+ nodes)."""
    agency = _user_agency(ctx)
    try:
        g = await queries.get_graph(params.case_id, max_nodes=200, min_mentions=1,
                                    agency_id=agency) or {}
        raw_nodes = g.get("nodes") or []
        raw_edges = g.get("edges") or []
        stats = g.get("stats") or {}
        node_count = int(stats.get("total_entities") or len(raw_nodes))
        edge_count = int(stats.get("total_edges") or len(raw_edges))

        def _top(n):
            d = n.get("data") if isinstance(n, dict) and "data" in n else n
            d = d if isinstance(d, dict) else {}
            return {
                "id": str(d.get("id", "")),
                "label": d.get("label") or d.get("value") or str(d.get("id", "")),
                "type": d.get("type"),
                "mention_count": d.get("mention_count"),
            }
        tops = [_top(n) for n in raw_nodes if n]
        tops.sort(key=lambda t: t.get("mention_count") or 0, reverse=True)
        tops = tops[:_LIST_CAP]
        note = (f"showing the top {len(tops)} of {node_count} entities"
                if node_count > len(tops) else None)
        return ActionResult.success(
            data={"case_id": params.case_id, "node_count": node_count,
                  "edge_count": edge_count, "top_entities": tops, "note": note},
            summary=(f"Intelligence graph for case {params.case_id}: "
                     f"{node_count} entities, {edge_count} relationships."
                     + (f" {note}." if note else "")),
        )
    except Exception as e:
        return ActionResult.error(f"Failed to load intelligence graph: {e}")


# ── Entities (list + single) ─────────────────────────────────────────────────────


@chat.function("list_entities", action_type="read",
               data_model=EntityListResponse,
               description=(
                   "List the entities extracted from a case (people, orgs, "
                   "accounts, ...), ranked by mention count. Optional type filter."
               ))
@require_unlock
async def fn_list_entities(ctx, params: ListEntitiesParams) -> ActionResult:
    agency = _user_agency(ctx)
    try:
        rows = await queries.list_entities(
            params.case_id, limit=_LIST_CAP,
            type_filter=(params.type_filter.strip() or None),
            min_mentions=params.min_mentions, agency_id=agency)
        # list_entities already applies the server-side limit; total reflects
        # the returned window (the API caps at limit). has_more=True iff full.
        rows = rows if isinstance(rows, list) else []
        return ActionResult.success(
            data={"items": rows[:_LIST_CAP], "total": len(rows),
                  "has_more": len(rows) >= _LIST_CAP, "case_id": params.case_id},
            summary=f"{len(rows)} entity(ies) in case {params.case_id}.",
        )
    except Exception as e:
        return ActionResult.error(f"Failed to list entities: {e}")


@chat.function("get_entity", action_type="read",
               data_model=EntityRecord,
               description="Get one extracted entity by its ID (type, value, mention count)")
@require_unlock
async def fn_get_entity(ctx, params: GetEntityParams) -> ActionResult:
    agency = _user_agency(ctx)
    try:
        e = await queries.get_entity(params.case_id, params.entity_id,
                                     agency_id=agency) or {}
        if not e:
            return ActionResult.error(
                f"Entity {params.entity_id} not found in case {params.case_id}.",
                retryable=False)
        return ActionResult.success(
            data=e,
            summary=(f"{e.get('type', 'Entity')}: "
                     f"{e.get('value') or e.get('normalized_value') or params.entity_id} "
                     f"({e.get('mention_count', 0)} mentions)."),
        )
    except Exception as e:
        return ActionResult.error(f"Failed to get entity: {e}")
