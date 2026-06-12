"""
Sharelock v2 — Cases API HTTP transport (leaf module, Rule-6 split 2026-06-12).

The shared GET/POST/PUT/DELETE helpers + error type used by every Cases API
read/write module (queries.py, queries_collab.py, queries_analysis.py).
Leaf on purpose: importing this module never pulls another queries_* module,
so the split is free of circular imports under ANY import order.

Callers outside the queries_* family should keep importing through
``queries`` (which re-exports everything here) — the single Cases-API door.
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


def _raise_for_error(r: httpx.Response) -> None:
    """Shared 4xx/5xx -> CasesAPIError translation for write helpers."""
    if r.status_code >= 400:
        detail = ""
        try:
            detail = r.json().get("detail", "")
        except Exception:
            detail = r.text[:200]
        raise CasesAPIError(r.status_code, detail)


async def _get(path: str, agency_id: Optional[str] = None):
    """GET helper. Returns JSON or empty list/dict on 404."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        r = await c.get(f"{CASES_API_URL}{path}", headers=_hdrs(agency_id))
        if r.status_code == 404:
            return [] if "?" in path else {}
        r.raise_for_status()
        return r.json()


async def _post(path: str, data: dict | None = None, params: dict | None = None,
                agency_id: Optional[str] = None,
                extra_headers: dict | None = None):
    """POST helper. Raises CasesAPIError on 4xx/5xx.

    ``extra_headers`` rides per-call headers (e.g. X-Imperal-User-ID for
    share grants) on top of the api-key + agency headers.
    """
    headers = _hdrs(agency_id)
    if extra_headers:
        headers.update(extra_headers)
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        r = await c.post(
            f"{CASES_API_URL}{path}",
            headers=headers,
            json=data or {},
            params=params or None,
        )
        _raise_for_error(r)
        return r.json()


async def _put(path: str, data: dict | None = None,
               agency_id: Optional[str] = None):
    """PUT helper. Raises CasesAPIError on 4xx/5xx (mirrors _post)."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        r = await c.put(f"{CASES_API_URL}{path}", headers=_hdrs(agency_id),
                        json=data or {})
        _raise_for_error(r)
        return r.json()


async def _delete(path: str, agency_id: Optional[str] = None):
    """DELETE helper. Raises CasesAPIError on 4xx/5xx (mirrors _post)."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        r = await c.delete(f"{CASES_API_URL}{path}", headers=_hdrs(agency_id))
        _raise_for_error(r)
        return r.json()
