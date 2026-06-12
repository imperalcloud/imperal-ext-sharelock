"""
Sharelock v2 — Cases API HTTP client (core case/file/analysis surface).

All HTTP calls to the Cases API (66.78.41.10:8096) go through the queries
family. No other module should import httpx or make direct HTTP calls to
Cases API. This module stays the single import door: it re-exports the
transport helpers (queries_http), the analysis read surface
(queries_analysis) and the collaboration/auth/agency surface
(queries_collab), so every ``queries.<fn>`` call site — and test
monkeypatching via ``queries.<fn>`` — works unchanged after the Rule-6
split (2026-06-12).

Agency scoping (rollout 2026-04-18):
-----------------------------------
Every public function accepts an optional ``agency_id`` kwarg. When provided,
the ``X-Imperal-Agency-ID`` header is attached so the Cases API can stamp the
row on write and (later, on enforcement) filter on read. Callers should pass
``ctx.user.agency_id or "default"``. Omitting the kwarg keeps the legacy
behaviour — Cases API logs a warning but does not fail.
"""
import logging
from typing import Optional

from queries_http import (  # noqa: F401 — re-export: transport + error type
    CasesAPIError,
    _delete,
    _get,
    _hdrs,
    _patch,
    _post,
    _put,
    _raise_for_error,
)
from queries_analysis import (  # noqa: F401 — re-export: analysis artifacts
    get_audit_log,
    get_entity,
    get_graph,
    get_latest_active_run,
    get_run,
    get_taxonomy,
    list_entities,
    list_events,
    list_gaps,
    list_inspections,
    list_relationships,
    list_runs,
    list_summaries,
    post_gap_decision,
    verify_audit,
)
from queries_collab import (  # noqa: F401 — re-export: shares/unlock/agency
    delete_case,
    delete_file,
    delete_share,
    get_agency_storage,
    get_shares,
    get_unlock,
    post_share,
    put_agency_storage,
    update_case,
)

log = logging.getLogger("sharelock-v2.queries")


# ── Cases ─────────────────────────────────────────────────────────────────────


async def get_cases(user_id: str, agency_id: Optional[str] = None) -> list:
    """Get all cases for a user."""
    resp = await _get(f"/cases?user_id={user_id}", agency_id=agency_id)
    return resp if isinstance(resp, list) else resp.get("cases", [])


async def get_case(case_id: int, agency_id: Optional[str] = None) -> dict:
    """Get a single case by ID."""
    return await _get(f"/cases/{case_id}", agency_id=agency_id)


async def create_case(user_id: str, name: str, description: str = "",
                      agency_id: Optional[str] = None) -> dict:
    """Create a new case (stamps agency_id when supplied)."""
    return await _post(
        "/cases",
        {"user_id": user_id, "name": name, "description": description},
        agency_id=agency_id,
    )


# ── Files ─────────────────────────────────────────────────────────────────────


async def get_files(case_id: int, agency_id: Optional[str] = None) -> list:
    """Get files for a case."""
    resp = await _get(f"/cases/{case_id}/files", agency_id=agency_id)
    return resp if isinstance(resp, list) else []


# ── Analysis ──────────────────────────────────────────────────────────────────


async def get_analysis(case_id: int, agency_id: Optional[str] = None) -> dict:
    """Get analysis status and result for a case."""
    return await _get(f"/cases/{case_id}/analysis", agency_id=agency_id)


async def start_analysis(case_id: int, user_id: str,
                         agency_id: Optional[str] = None) -> dict:
    """Start deep analysis for a case. Raises CasesAPIError(409, ...) on active run."""
    return await _post(f"/cases/{case_id}/analysis?user_id={user_id}",
                       agency_id=agency_id)


async def cancel_analysis(case_id: int, actor: str, reason: str = "user_cancelled",
                          agency_id: Optional[str] = None) -> dict:
    """Cancel the latest active analysis run."""
    return await _post(
        f"/cases/{case_id}/analysis/cancel",
        {"actor": actor, "reason": reason},
        agency_id=agency_id,
    )


# ── Report ────────────────────────────────────────────────────────────────────


async def sign_report_url(
    case_id: int, run_id: int, fmt: str = "pdf", ttl: int = 600,
    agency_id: Optional[str] = None,
) -> dict:
    """Mint a short-lived HMAC-signed URL for a browser-initiated report download."""
    params = {"run_id": run_id, "format": fmt, "ttl": ttl}
    return await _post(f"/cases/{case_id}/report/sign", data=None, params=params,
                       agency_id=agency_id)
