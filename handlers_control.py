"""
Sharelock v2 — Track D control handlers: report link + destructive deletes + rename.

D2 get_report (read): resolve the latest completed run and mint a short-lived
HMAC-signed report URL via the Cases API; no completed run → a typed
"not available yet" SUCCESS fact (never an error). Webbee hands the user the
clickable link.

D3 delete_case / delete_file (destructive): declared ``action_type="destructive"``
so the KERNEL auto-inserts the confirmation card BEFORE the handler runs
(pattern: admin ext delete_role). The handler just does the work + returns the
receipt. Case name/id are passed VERBATIM (I-CHAT-FUNCTION-VERBATIM-PARAMS).

D4 update_case (write): PATCH name/description.

All @chat.function tools are dispatched by hub routing; every tool is
@require_unlock gated and threads agency_id = _user_agency(ctx).
"""
import logging

from pydantic import BaseModel, Field

from app import chat, _user_agency
from auth_gate import require_unlock
from imperal_sdk.chat import ActionResult
import queries
from queries import CasesAPIError
from models import (
    ReportLink, CaseDeleteReceipt, FileDeleteReceipt, UpdateCaseReceipt,
)

log = logging.getLogger("sharelock-v2.handlers_control")


# ── Parameter Models ──────────────────────────────────────────────────────────


class GetReportParams(BaseModel):
    case_id: int = Field(..., description="Case ID to mint a forensic report link for")


class DeleteCaseParams(BaseModel):
    case_id: int = Field(..., description=(
        "Case ID to delete. Pass the user's VERBATIM case id — the kernel "
        "shows a confirmation card before this runs (destructive action)."
    ))


class DeleteFileParams(BaseModel):
    case_id: int = Field(..., description="Case ID the file belongs to")
    file_id: int = Field(..., description=(
        "Evidence file ID to delete. Pass VERBATIM — the kernel shows a "
        "confirmation card before this runs (destructive action)."
    ))


class UpdateCaseParams(BaseModel):
    case_id: int = Field(..., description="Case ID to rename / re-describe")
    name: str = Field("", description=(
        "New case name. CRITICAL: pass VERBATIM in the user's original "
        "language (RU/EN). Do NOT paraphrase, translate, or auto-title. "
        "Empty string = leave the name unchanged."
    ))
    description: str = Field("", description=(
        "New case description. Pass VERBATIM. Empty string = leave unchanged."
    ))


# ── D2: report link ─────────────────────────────────────────────────────────────


@chat.function("get_report", action_type="read",
               data_model=ReportLink,
               description=(
                   "Get a downloadable forensic report link for a case. "
                   "Returns a short-lived signed PDF URL once analysis has "
                   "completed; otherwise reports that analysis must run first."
               ))
@require_unlock
async def fn_get_report(ctx, params: GetReportParams) -> ActionResult:
    """Mint a signed report URL for the latest completed run, or a typed
    'not available yet' SUCCESS fact when no completed run exists."""
    case_id = params.case_id
    agency = _user_agency(ctx)
    try:
        case = await queries.get_case(case_id, agency_id=agency)
        if not case or not case.get("name"):
            return ActionResult.error(f"Case {case_id} not found.", retryable=False)
        case_name = case.get("name")

        # Resolve the run to report on: the case's active_run_id, else the
        # analysis record's run, else the latest run.
        run_id = case.get("active_run_id")
        analysis = await queries.get_analysis(case_id, agency_id=agency)
        # /cases/{id}/analysis returns BOTH `analysis_status` (the analysis
        # lifecycle = "completed") and `status` (the CASE lifecycle = "active").
        # Read `analysis_status` FIRST — reading `status` first wrongly reports
        # "no completed analysis" for an active case with a finished analysis.
        a_status = (analysis or {}).get("analysis_status") or (analysis or {}).get("status")
        if not run_id:
            run_id = (analysis or {}).get("active_run_id")
        if not run_id:
            latest = await queries.get_latest_active_run(case_id, agency_id=agency)
            run_id = (latest or {}).get("run_id")
            a_status = a_status or (latest or {}).get("status")

        if not run_id or (a_status and str(a_status).lower() not in
                          ("completed", "complete", "done", "finished")):
            return ActionResult.success(
                data={"available": False, "case_id": case_id,
                      "title": f"Report — {case_name}",
                      "reason": "no completed analysis yet — run analysis first"},
                summary=(f"No completed analysis for case **{case_name}** yet — "
                         f"run analysis first, then I can produce the report."),
            )

        signed = await queries.sign_report_url(
            case_id, run_id, fmt="pdf", ttl=600, agency_id=agency)
        url = (signed or {}).get("url")
        if not url:
            return ActionResult.error(
                "Report signing returned no URL — try again shortly.")
        return ActionResult.success(
            data={"available": True, "id": str(case_id),
                  "title": f"Report — {case_name}", "url": url,
                  "format": "pdf", "expires_in_seconds": 600, "run_id": run_id},
            summary=(f"Forensic report for **{case_name}** is ready: {url}\n"
                     f"This link expires in 10 minutes."),
        )
    except CasesAPIError as e:
        return ActionResult.error(f"Failed to get report: {e.detail or e}",
                                  retryable=False)
    except Exception as e:
        return ActionResult.error(f"Failed to get report: {e}")


# ── D3: destructive deletes (kernel auto-confirms) ──────────────────────────────


@chat.function("delete_case", action_type="destructive",
               effects=["delete:case"],
               data_model=CaseDeleteReceipt,
               description=(
                   "Permanently delete an investigation case and clean its "
                   "evidence files. Pass the case id VERBATIM. The platform "
                   "shows a confirmation card before this runs."
               ))
@require_unlock
async def fn_delete_case(ctx, params: DeleteCaseParams) -> ActionResult:
    """Delete a case after the kernel-rendered confirmation. Resolves the case
    first so the receipt carries the verbatim name."""
    case_id = params.case_id
    agency = _user_agency(ctx)
    try:
        case = await queries.get_case(case_id, agency_id=agency)
        if not case or not case.get("name"):
            return ActionResult.error(f"Case {case_id} not found.", retryable=False)
        case_name = case.get("name")
        result = await queries.delete_case(case_id, agency_id=agency)
        deleted = bool(result.get("deleted", True))
        note = "case moved to deleted; evidence files cleaned"
        return ActionResult.success(
            data={"deleted": deleted, "case_id": case_id, "title": case_name,
                  "note": note},
            summary=(f"Case **{case_name}** (ID: {case_id}) deleted — {note}."),
        )
    except CasesAPIError as e:
        return ActionResult.error(f"Delete failed: {e.detail or e}", retryable=False)
    except Exception as e:
        return ActionResult.error(f"Delete failed: {e}")


@chat.function("delete_file", action_type="destructive",
               effects=["delete:file"],
               data_model=FileDeleteReceipt,
               description=(
                   "Permanently delete one evidence file from a case. Pass the "
                   "file id VERBATIM. The platform shows a confirmation card "
                   "before this runs."
               ))
@require_unlock
async def fn_delete_file(ctx, params: DeleteFileParams) -> ActionResult:
    """Delete one evidence file after the kernel-rendered confirmation."""
    case_id = params.case_id
    file_id = params.file_id
    agency = _user_agency(ctx)
    try:
        result = await queries.delete_file(case_id, file_id, agency_id=agency)
        deleted = bool(result.get("deleted", True))
        return ActionResult.success(
            data={"deleted": deleted, "case_id": case_id, "file_id": file_id},
            summary=(f"Evidence file {file_id} deleted from case {case_id}."),
        )
    except CasesAPIError as e:
        return ActionResult.error(f"Delete failed: {e.detail or e}", retryable=False)
    except Exception as e:
        return ActionResult.error(f"Delete failed: {e}")


# ── D4: rename / re-describe (write) ────────────────────────────────────────────


@chat.function("update_case", action_type="write",
               effects=["update:case"],
               data_model=UpdateCaseReceipt,
               description=(
                   "Rename or re-describe an investigation case. CRITICAL: "
                   "pass `name` and `description` VERBATIM in the user's "
                   "original language. Empty string = leave that field unchanged."
               ))
@require_unlock
async def fn_update_case(ctx, params: UpdateCaseParams) -> ActionResult:
    """PATCH the case name/description (only the supplied fields are sent)."""
    case_id = params.case_id
    name = params.name.strip() or None
    description = params.description.strip() or None
    if name is None and description is None:
        return ActionResult.error(
            "Provide a new name or description to update.", retryable=False)
    agency = _user_agency(ctx)
    try:
        result = await queries.update_case(
            case_id, name=name, description=description, agency_id=agency)
        new_name = (result or {}).get("name") or name
        return ActionResult.success(
            data={"updated": True, "case_id": case_id, "name": new_name,
                  "description": description},
            summary=(f"Case {case_id} updated"
                     + (f" — name is now **{new_name}**." if name else ".")),
        )
    except CasesAPIError as e:
        return ActionResult.error(f"Update failed: {e.detail or e}", retryable=False)
    except Exception as e:
        return ActionResult.error(f"Update failed: {e}")
