"""
Sharelock v2 — Cases API HTTP client.

All HTTP calls to the Cases API (66.78.41.10:8096) go through this module.
No other module should import httpx or make direct HTTP calls to Cases API.

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

import httpx

from app import CASES_API_URL, CASES_API_KEY

log = logging.getLogger("sharelock-v2.queries")

_BASE_HEADERS = {"x-api-key": CASES_API_KEY, "Content-Type": "application/json"}
_TIMEOUT = 60.0


def _hdrs(agency_id: Optional[str] = None) -> dict:
    """Build request headers. Attaches X-Imperal-Agency-ID if supplied.

    Never raises on None — rollout tolerates missing agency_id and the Cases
    API logs a warning server-side.
    """
    h = dict(_BASE_HEADERS)
    if agency_id:
        h["X-Imperal-Agency-ID"] = str(agency_id)
    return h


class CasesAPIError(Exception):
    """Cases API returned non-2xx. `status` is HTTP status; `detail` is best-effort detail."""

    def __init__(self, status: int, detail: str = ""):
        self.status = status
        self.detail = detail
        super().__init__(f"Cases API {status}: {detail}")


async def _get(path: str, agency_id: Optional[str] = None):
    """GET helper. Returns JSON or empty list/dict on 404."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        r = await c.get(f"{CASES_API_URL}{path}", headers=_hdrs(agency_id))
        if r.status_code == 404:
            return [] if "?" in path else {}
        r.raise_for_status()
        return r.json()


async def _post(path: str, data: dict | None = None, params: dict | None = None,
                agency_id: Optional[str] = None):
    """POST helper. Raises CasesAPIError on 4xx/5xx."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        r = await c.post(
            f"{CASES_API_URL}{path}",
            headers=_hdrs(agency_id),
            json=data or {},
            params=params or None,
        )
        if r.status_code >= 400:
            detail = ""
            try:
                detail = r.json().get("detail", "")
            except Exception:
                detail = r.text[:200]
            raise CasesAPIError(r.status_code, detail)
        return r.json()


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


async def list_runs(case_id: int, agency_id: Optional[str] = None) -> list:
    """List all analysis runs for a case, newest version first."""
    resp = await _get(f"/cases/{case_id}/runs", agency_id=agency_id)
    return resp if isinstance(resp, list) else []


async def get_run(case_id: int, run_id: int, agency_id: Optional[str] = None) -> dict:
    """Fetch a single run."""
    return await _get(f"/cases/{case_id}/runs/{run_id}", agency_id=agency_id)


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


# ── Members ───────────────────────────────────────────────────────────────────


async def get_members(case_id: int, agency_id: Optional[str] = None) -> list:
    """Get case team members."""
    resp = await _get(f"/cases/{case_id}/members", agency_id=agency_id)
    return resp if isinstance(resp, list) else []


# ── Report ────────────────────────────────────────────────────────────────────


async def sign_report_url(
    case_id: int, run_id: int, fmt: str = "pdf", ttl: int = 600,
    agency_id: Optional[str] = None,
) -> dict:
    """Mint a short-lived HMAC-signed URL for a browser-initiated report download."""
    params = {"run_id": run_id, "format": fmt, "ttl": ttl}
    return await _post(f"/cases/{case_id}/report/sign", data=None, params=params,
                       agency_id=agency_id)


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


# ── Helpers ───────────────────────────────────────────────────────────────────


async def get_latest_active_run(case_id: int, agency_id: Optional[str] = None) -> dict:
    """Return the latest run for a case (or {} if none). Newest first by version."""
    runs = await list_runs(case_id, agency_id=agency_id)
    return runs[0] if runs else {}
