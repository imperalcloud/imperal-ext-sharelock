"""
Sharelock v2 — Per-case share handlers (Track C.2 T3).

Visibility model (Valentin, 2026-06-12): a case is PRIVATE to its creator
until explicitly shared; sharelock-role 'admin' is the agency supervisor and
sees everything. Grants live in the Cases API ``case_shares`` table keyed by
imperal_id; this module is the chat-side door (share / unshare / list).
All @chat.function tools are dispatched by hub routing.
"""
import logging

from pydantic import BaseModel, Field

from app import chat, _user_id, _user_agency
from auth_gate import require_unlock
from imperal_sdk.chat import ActionResult
import queries
from queries import CasesAPIError
from models import ShareCaseResponse, UnshareCaseResponse, CaseShareListResponse

log = logging.getLogger("sharelock-v2.handlers_share")


# ── Parameter Models ──────────────────────────────────────────────────────────


class ShareCaseParams(BaseModel):
    case_id: int = Field(..., description="Case ID to share")
    colleague: str = Field(..., description=(
        "Colleague identifier — their imperal id (imp_u_...). Pass VERBATIM "
        "as the user supplied it. Do NOT paraphrase or normalise; the grant "
        "is stored keyed by this exact string."
    ))


class UnshareCaseParams(BaseModel):
    case_id: int = Field(..., description="Case ID to revoke access on")
    colleague: str = Field(..., description=(
        "Colleague identifier the grant was created with (imp_u_...). Pass "
        "VERBATIM — revocation matches the stored grant key exactly."
    ))


class CaseSharesParams(BaseModel):
    case_id: int = Field(..., description="Case ID to list share grants for")


# ── Chat Functions ────────────────────────────────────────────────────────────


@chat.function("share_case", action_type="write",
               effects=["create:case_share"],
               data_model=ShareCaseResponse,
               description=(
                   "Share an investigation case with a colleague in the same "
                   "agency by their imperal id (imp_u_...). Pass `colleague` "
                   "VERBATIM as the user supplied it."
               ))
@require_unlock
async def fn_share_case(ctx, params: ShareCaseParams) -> ActionResult:
    """Grant a colleague access to a case.

    v1 passes ``colleague`` to the Cases API AS-IS as the grant's
    ``imperal_id`` (the share body's only key). TODO(controller): the Cases
    API will resolve emails server-side later; until then the e2e flow uses
    the colleague's imperal id as shown by list_case_shares. When the input
    doesn't look like an imperal id we still store it verbatim and say so
    honestly in the fact (``note``).
    """
    colleague = params.colleague.strip()
    if not colleague:
        return ActionResult.error("A colleague identifier is required.",
                                  retryable=False)
    agency = _user_agency(ctx)
    try:
        case = await queries.get_case(params.case_id, agency_id=agency)
        if not case or not case.get("name"):
            return ActionResult.error(f"Case {params.case_id} not found.",
                                      retryable=False)
        case_name = case.get("name")
        await queries.post_share(params.case_id, colleague,
                                 granted_by=_user_id(ctx), agency_id=agency)
        note = None
        if not colleague.startswith("imp_"):
            note = ("identifier does not look like an imperal id (imp_u_...) "
                    "— stored verbatim; email lookup lands server-side later")
        return ActionResult.success(
            data={"shared": True, "case_id": params.case_id,
                  "imperal_id": colleague, "note": note},
            summary=(f"Case **{case_name}** (ID: {params.case_id}) shared "
                     f"with {colleague}."
                     + (f" Note: {note}." if note else "")),
        )
    except CasesAPIError as e:
        return ActionResult.error(f"Share failed: {e.detail or e}",
                                  retryable=False)
    except Exception as e:
        return ActionResult.error(f"Share failed: {e}")


@chat.function("unshare_case", action_type="write",
               effects=["delete:case_share"],
               data_model=UnshareCaseResponse,
               description=(
                   "Revoke a colleague's access to an investigation case. "
                   "Pass `colleague` VERBATIM — the imperal id the grant "
                   "was created with."
               ))
@require_unlock
async def fn_unshare_case(ctx, params: UnshareCaseParams) -> ActionResult:
    """Revoke a share grant. deleted=0 from the API means no grant existed."""
    colleague = params.colleague.strip()
    if not colleague:
        return ActionResult.error("A colleague identifier is required.",
                                  retryable=False)
    agency = _user_agency(ctx)
    try:
        result = await queries.delete_share(params.case_id, colleague,
                                            agency_id=agency)
        deleted = int(result.get("deleted") or 0)
        return ActionResult.success(
            data={"unshared": deleted > 0, "deleted": deleted,
                  "case_id": params.case_id, "imperal_id": colleague},
            summary=(f"Access revoked for {colleague} on case "
                     f"{params.case_id}." if deleted else
                     f"No grant found for {colleague} on case "
                     f"{params.case_id} — nothing to revoke."),
        )
    except CasesAPIError as e:
        return ActionResult.error(f"Revoke failed: {e.detail or e}",
                                  retryable=False)
    except Exception as e:
        return ActionResult.error(f"Revoke failed: {e}")


@chat.function("list_case_shares", action_type="read",
               data_model=CaseShareListResponse,
               description=(
                   "List who an investigation case is shared with — the "
                   "owner plus every colleague grant (imperal id + email)."
               ))
@require_unlock
async def fn_list_case_shares(ctx, params: CaseSharesParams) -> ActionResult:
    """Share grants for a case as a real sdl.EntityList[CaseShareRecord]."""
    agency = _user_agency(ctx)
    try:
        resp = await queries.get_shares(params.case_id, agency_id=agency)
        shares = [s for s in (resp.get("shares") or []) if isinstance(s, dict)]
        owner = resp.get("owner")

        lines = []
        if owner:
            owner_label = (owner.get("email") or owner.get("name")
                           or owner.get("imperal_id") or "unknown")
            lines.append(f"Owner: **{owner_label}** "
                         f"({owner.get('imperal_id') or '—'})")
        for s in shares:
            label = (s.get("email") or s.get("name")
                     or s.get("imperal_id") or "?")
            lines.append(f"- {label} ({s.get('imperal_id') or '—'})")
        if not shares:
            lines.append("Not shared with anyone yet.")

        return ActionResult.success(
            data={"items": shares, "case_id": params.case_id, "owner": owner},
            summary="\n".join(lines),
        )
    except CasesAPIError as e:
        return ActionResult.error(f"Failed to list shares: {e.detail or e}",
                                  retryable=False)
    except Exception as e:
        return ActionResult.error(f"Failed to list shares: {e}")
