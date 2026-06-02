"""
Sharelock v2 — Analysis-run handlers.

Handlers for run lifecycle (start/cancel) and gap-review decisions.
All @chat.function tools are dispatched by hub routing.
B2 (run_analysis 409), B3 (cancel_analysis), gap-review flow.
"""
import logging

from pydantic import BaseModel, Field

from app import chat, _user_id, _user_agency
from imperal_sdk.chat import ActionResult
import queries
from queries import CasesAPIError
from models import (
    GapReviewResponse,
    RunAnalysisResponse, CancelAnalysisResponse, GapDecisionResponse,
)

log = logging.getLogger("sharelock-v2.handlers_analysis")


# ── Parameter Models ──────────────────────────────────────────────────────────


class CaseIdParams(BaseModel):
    case_id: int = Field(..., description="Case ID")


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _latest_run_or_error(case_id: int, agency_id: str | None = None):
    """Return (run_id, run, error_result). If error_result is truthy, return it."""
    latest = await queries.get_latest_active_run(case_id, agency_id=agency_id)
    if not latest:
        return None, None, ActionResult.error(
            f"No analysis runs yet for case {case_id}. Run Analysis first.",
            retryable=False,
        )
    return latest.get("run_id"), latest, None


# ── Run lifecycle ─────────────────────────────────────────────────────────────


@chat.function("run_analysis", action_type="write",
               effects=["run:analysis"],
               data_model=RunAnalysisResponse,
               description="Start deep forensic analysis on a case")
async def fn_run_analysis(ctx, params: CaseIdParams) -> ActionResult:
    """Signal session workflow to start analysis. B2: handle 409 from Cases API."""
    user_id = _user_id(ctx)
    agency = _user_agency(ctx)
    try:
        result = await queries.start_analysis(params.case_id, user_id, agency_id=agency)
        run_id = result.get("run_id")
        version = result.get("version")
        return ActionResult.success(
            data={"case_id": params.case_id, "status": "started",
                  "run_id": run_id, "version": version},
            summary=(f"Deep forensic analysis started for case {params.case_id} "
                     f"(run #{run_id}, v{version}). This typically takes 2-5 minutes. "
                     f"You will be notified when complete."),
        )
    except CasesAPIError as e:
        if e.status == 409:
            return ActionResult.error(
                "Analysis already running for this case. "
                "View progress in the Analysis tab, or cancel the current run first.",
                retryable=False,
            )
        return ActionResult.error(f"Failed to start analysis: {e.detail or e}",
                                  retryable=False)
    except Exception as e:
        return ActionResult.error(f"Failed to start analysis: {e}")


@chat.function("cancel_analysis", action_type="write",
               effects=["cancel:analysis"],
               data_model=CancelAnalysisResponse,
               description="Cancel the current analysis run for a case")
async def fn_cancel_analysis(ctx, params: CaseIdParams) -> ActionResult:
    """Cancel the latest active analysis run. B3. Uses imperal_id as actor."""
    actor = _user_id(ctx) or "unknown"
    agency = _user_agency(ctx)
    try:
        result = await queries.cancel_analysis(params.case_id, actor=actor,
                                               reason="user_cancelled",
                                               agency_id=agency)
        run_id = result.get("run_id") or result.get("cancelled_run_id") or "?"
        return ActionResult.success(
            data={"case_id": params.case_id, "run_id": run_id, "status": "cancelled"},
            summary=(f"Analysis cancelled for case {params.case_id} (run #{run_id}). "
                     f"You can start a new run when ready."),
        )
    except CasesAPIError as e:
        if e.status == 400:
            return ActionResult.error(
                "No active analysis run to cancel. The run may have already finished.",
                retryable=False,
            )
        if e.status == 404:
            return ActionResult.error(f"Case {params.case_id} not found.",
                                      retryable=False)
        return ActionResult.error(f"Cancel failed: {e.detail or e}", retryable=False)
    except Exception as e:
        return ActionResult.error(f"Cancel failed: {e}")


# ── Gap review ────────────────────────────────────────────────────────────────


@chat.function("review_analysis_gaps", action_type="read",
               data_model=GapReviewResponse,
               description="Review gaps found during analysis")
async def fn_review_analysis_gaps(ctx, params: CaseIdParams) -> ActionResult:
    """Fetch gaps for the latest run, format chat summary + structured data.

    The gap-review panel consumes data.by_severity / data.confidence_* when
    rendering. The chat summary is always a formatted markdown string.
    """
    case_id = params.case_id
    agency = _user_agency(ctx)
    try:
        run_id, latest, err = await _latest_run_or_error(case_id, agency_id=agency)
        if err is not None:
            return err
        gaps = await queries.list_gaps(case_id, run_id, agency_id=agency)
        by_severity: dict[str, list] = {"BLOCKING": [], "QUALITY": [], "INFORMATIONAL": []}
        for g in gaps:
            by_severity.setdefault(g.get("severity", "INFORMATIONAL"), []).append(g)

        confidence_current = latest.get("confidence_current")
        confidence_potential = latest.get("confidence_potential")

        parts = [f"**Gap Review — Case {case_id}, Run #{run_id}**"]
        if confidence_current is not None:
            cur_pct = f"{float(confidence_current):.0%}"
            if confidence_potential is not None:
                pot_pct = f"{float(confidence_potential):.0%}"
                parts.append(f"Confidence: **{cur_pct}** now → **{pot_pct}** potential")
            else:
                parts.append(f"Confidence: **{cur_pct}**")

        for sev in ("BLOCKING", "QUALITY", "INFORMATIONAL"):
            items = by_severity.get(sev, [])
            if not items:
                continue
            parts.append(f"\n**{sev}** ({len(items)}):")
            for g in items[:10]:
                desc = (g.get("description") or "").strip().split("\n")[0][:200]
                parts.append(f"- {desc}")
            if len(items) > 10:
                parts.append(f"- ...and {len(items) - 10} more")

        if not gaps:
            parts.append("\nNo gaps flagged. Analysis can continue.")

        # SDL entity-list (NO legacy {gaps} wrapper): the flat gap list flows
        # through data["items"]; each gap is a canonical SDL entity
        # (id, title=description, kind="gap"). Conforms to
        # sdl.EntityList[GapReviewItem] (x-sdl="entity-list"). The platform
        # scalars + the severity-bucketed by_severity map are kept as additive
        # fields on the EntityList subclass.
        return ActionResult.success(
            data={
                "items": gaps,
                "case_id": case_id,
                "run_id": run_id,
                "by_severity": by_severity,
                "confidence_current": confidence_current,
                "confidence_potential": confidence_potential,
            },
            summary="\n".join(parts),
        )
    except CasesAPIError as e:
        return ActionResult.error(f"Failed to fetch gaps: {e.detail or e}", retryable=False)
    except Exception as e:
        return ActionResult.error(f"Failed to fetch gaps: {e}")


@chat.function("continue_analysis", action_type="write",
               effects=["continue:analysis"],
               data_model=GapDecisionResponse,
               description="Continue analysis despite flagged gaps")
async def fn_continue_analysis(ctx, params: CaseIdParams) -> ActionResult:
    """Signal decision=continue on the latest active run."""
    case_id = params.case_id
    agency = _user_agency(ctx)
    try:
        run_id, _latest, err = await _latest_run_or_error(case_id, agency_id=agency)
        if err is not None:
            return err
        await queries.post_gap_decision(case_id, run_id, decision="continue",
                                        reasoning="Operator chose to continue despite gaps",
                                        agency_id=agency)
        return ActionResult.success(
            data={"case_id": case_id, "run_id": run_id, "decision": "continue"},
            summary=(f"Continuing analysis for case {case_id} (run #{run_id}) "
                     f"with current evidence. The analysis will proceed through "
                     f"remaining phases."),
        )
    except CasesAPIError as e:
        return ActionResult.error(f"Continue signal failed: {e.detail or e}", retryable=False)
    except Exception as e:
        return ActionResult.error(f"Continue signal failed: {e}")


@chat.function("resume_with_new_evidence", action_type="write",
               effects=["pause:analysis"],
               data_model=GapDecisionResponse,
               description="Pause analysis so you can upload more evidence, then run again")
async def fn_resume_with_new_evidence(ctx, params: CaseIdParams) -> ActionResult:
    """Signal decision=add_evidence. Returns guidance for upload + rerun."""
    case_id = params.case_id
    agency = _user_agency(ctx)
    try:
        run_id, _latest, err = await _latest_run_or_error(case_id, agency_id=agency)
        if err is not None:
            return err
        await queries.post_gap_decision(case_id, run_id, decision="add_evidence",
                                        reasoning="Operator will upload additional evidence",
                                        agency_id=agency)
        return ActionResult.success(
            data={"case_id": case_id, "run_id": run_id, "decision": "add_evidence"},
            summary=(f"Analysis paused for case {case_id} (run #{run_id}). "
                     f"Upload the missing evidence to the Nextcloud folder, "
                     f"then click Run Analysis to start a new versioned run."),
        )
    except CasesAPIError as e:
        return ActionResult.error(f"Signal failed: {e.detail or e}", retryable=False)
    except Exception as e:
        return ActionResult.error(f"Signal failed: {e}")
