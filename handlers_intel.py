"""
Sharelock v2 — Track D4 intelligence / forensic read handlers.

The forensic drill-down half of Track D4: relationships, timeline events,
taxonomy, the audit / chain-of-custody log (with hash-chain verification), and
analysis runs. Split out of handlers_drilldown.py to keep each module under the
300-LOC ceiling (CLAUDE.md Rule 6); the shared row→EntityList helper
(``_entity_list``), the list cap, and the common ``CaseIdParams`` live in
handlers_drilldown.py and are imported here at module level (no-lazy-import rule).

Every tool is @require_unlock gated, action_type='read', declares data_model=
(V23), caps lists at <=50 with native total/has_more, and threads
agency_id = _user_agency(ctx).
"""
import logging

from app import chat, _user_agency
from auth_gate import require_unlock
from imperal_sdk.chat import ActionResult
import queries
from handlers_drilldown import CaseIdParams, _LIST_CAP, _entity_list
from models import (
    RelationshipListResponse, TimelineEventListResponse, TaxonomyListResponse,
    AuditLogResponse, AnalysisRunListResponse,
)

log = logging.getLogger("sharelock-v2.handlers_intel")


# ── Relationships / timeline events / taxonomy ──────────────────────────────────


@chat.function("list_relationships", action_type="read",
               data_model=RelationshipListResponse,
               description="List the relationships between entities in a case")
@require_unlock
async def fn_list_relationships(ctx, params: CaseIdParams) -> ActionResult:
    agency = _user_agency(ctx)
    try:
        rows = await queries.list_relationships(
            params.case_id, limit=_LIST_CAP, agency_id=agency)
        rows = rows if isinstance(rows, list) else []
        return ActionResult.success(
            data={"items": rows[:_LIST_CAP], "total": len(rows),
                  "has_more": len(rows) >= _LIST_CAP, "case_id": params.case_id},
            summary=f"{len(rows)} relationship(s) in case {params.case_id}.",
        )
    except Exception as e:
        return ActionResult.error(f"Failed to list relationships: {e}")


@chat.function("list_timeline_events", action_type="read",
               data_model=TimelineEventListResponse,
               description="List the timeline events reconstructed for a case")
@require_unlock
async def fn_list_timeline_events(ctx, params: CaseIdParams) -> ActionResult:
    agency = _user_agency(ctx)
    try:
        rows = await queries.list_events(
            params.case_id, limit=_LIST_CAP, agency_id=agency)
        rows = rows if isinstance(rows, list) else []
        return ActionResult.success(
            data={"items": rows[:_LIST_CAP], "total": len(rows),
                  "has_more": len(rows) >= _LIST_CAP, "case_id": params.case_id},
            summary=f"{len(rows)} timeline event(s) in case {params.case_id}.",
        )
    except Exception as e:
        return ActionResult.error(f"Failed to list timeline events: {e}")


@chat.function("get_taxonomy", action_type="read",
               data_model=TaxonomyListResponse,
               description="List the OSAC evidence taxonomy categories for a case")
@require_unlock
async def fn_get_taxonomy(ctx, params: CaseIdParams) -> ActionResult:
    agency = _user_agency(ctx)
    try:
        rows = await queries.get_taxonomy(params.case_id, agency_id=agency)
        data = _entity_list(rows, params.case_id)
        return ActionResult.success(
            data=data,
            summary=f"{data['total']} taxonomy categor(ies) in case {params.case_id}.",
        )
    except Exception as e:
        return ActionResult.error(f"Failed to load taxonomy: {e}")


# ── Audit log (chain of custody) + analysis runs ────────────────────────────────


@chat.function("get_audit_log", action_type="read",
               data_model=AuditLogResponse,
               description=(
                   "Show the chain-of-custody audit log for a case — every "
                   "recorded action with actor and timestamp."
               ))
@require_unlock
async def fn_get_audit_log(ctx, params: CaseIdParams) -> ActionResult:
    agency = _user_agency(ctx)
    try:
        rows = await queries.get_audit_log(
            params.case_id, limit=_LIST_CAP, agency_id=agency)
        data = _entity_list(rows, params.case_id)
        verified = None
        try:
            verdict = await queries.verify_audit(params.case_id, agency_id=agency)
            if isinstance(verdict, dict) and verdict:
                verified = bool(verdict.get("valid", verdict.get("verified")))
        except Exception as ve:
            log.debug(f"verify_audit({params.case_id}) failed: {ve}")
        data["verified"] = verified
        integ = ("" if verified is None else
                 (" Chain integrity: verified." if verified
                  else " Chain integrity: FAILED."))
        return ActionResult.success(
            data=data,
            summary=(f"{data['total']} audit event(s) for case {params.case_id}."
                     + integ),
        )
    except Exception as e:
        return ActionResult.error(f"Failed to load audit log: {e}")


@chat.function("list_analysis_runs", action_type="read",
               data_model=AnalysisRunListResponse,
               description="List the analysis runs for a case (newest version first)")
@require_unlock
async def fn_list_analysis_runs(ctx, params: CaseIdParams) -> ActionResult:
    agency = _user_agency(ctx)
    try:
        rows = await queries.list_runs(params.case_id, agency_id=agency)
        data = _entity_list(rows, params.case_id)
        return ActionResult.success(
            data=data,
            summary=f"{data['total']} analysis run(s) for case {params.case_id}.",
        )
    except Exception as e:
        return ActionResult.error(f"Failed to list analysis runs: {e}")
