"""
Sharelock v2 — Cases API client: collaboration + auth + agency settings.

Rule-6 split of queries.py (2026-06-12): share grants, the Track-A unlock
read, and per-agency storage settings live here. ``queries`` re-exports
every function below, so ALL existing ``queries.<fn>`` call sites (and test
monkeypatching via ``queries.<fn>``) keep working unchanged.

Transport comes from the leaf ``queries_http`` module — never from
``queries`` — so there is no circular import under any import order.
"""
import logging
from typing import Optional
from urllib.parse import quote

from queries_http import _delete, _get, _post, _put

log = logging.getLogger("sharelock-v2.queries")


# ── Shares (Track C.2 — per-case grants within the agency) ───────────────────


async def post_share(case_id: int, imperal_id: str, granted_by: str,
                     agency_id: Optional[str] = None) -> dict:
    """Grant a colleague access to a case (POST /cases/{id}/share).

    The Cases API stores the grant keyed by ``imperal_id`` VERBATIM and
    records ``granted_by`` from the X-Imperal-User-ID header.
    """
    return await _post(
        f"/cases/{case_id}/share",
        {"imperal_id": imperal_id},
        agency_id=agency_id,
        extra_headers={"X-Imperal-User-ID": str(granted_by or "service")[:64]},
    )


async def delete_share(case_id: int, imperal_id: str,
                       agency_id: Optional[str] = None) -> dict:
    """Revoke a grant (DELETE /cases/{id}/share/{imperal_id}).

    Returns ``{"ok": True, "deleted": 0|1}`` — deleted=0 means no grant
    existed for that imperal_id.
    """
    return await _delete(
        f"/cases/{case_id}/share/{quote(str(imperal_id), safe='')}",
        agency_id=agency_id,
    )


async def get_shares(case_id: int, agency_id: Optional[str] = None) -> dict:
    """Share grants for a case (GET /cases/{id}/shares).

    Returns ``{"case_id", "owner": {imperal_id,email,name}|None,
    "shares": [{imperal_id, granted_by, created_at, email, name}]}``.
    """
    resp = await _get(f"/cases/{case_id}/shares", agency_id=agency_id)
    if isinstance(resp, dict) and resp:
        return resp
    return {"case_id": case_id, "owner": None, "shares": []}


# ── Auth unlock (Track A login) ───────────────────────────────────────────────


async def get_unlock(imperal_id: str) -> dict:
    """Live Sharelock unlock state for an imperal_id (service-key gated).

    Returns ``{"unlocked": bool, "agency_id": str, "role": str}`` —
    consumed by auth_gate._fetch_unlock (the @require_unlock gate).
    """
    resp = await _get(f"/auth/unlock/{imperal_id}")
    return resp if isinstance(resp, dict) else {"unlocked": False}


# ── Agency storage settings (Track B) ─────────────────────────────────────────


async def get_agency_storage(agency_id: str) -> dict:
    """Decrypted per-agency storage settings from the Cases API.

    ``{"configured": False}`` when the agency has no row — callers fall
    back to the NC_* env (default-agency storage). Credentials in the
    response are held in-process only (files.get_agency_backend);
    NEVER write them into ctx.cache.
    """
    resp = await _get(f"/agency/{agency_id}/storage")
    return resp if isinstance(resp, dict) else {"configured": False}


async def put_agency_storage(agency_id: str, body: dict) -> dict:
    """Replace per-agency settings (PUT /agency/{agency_id}/storage).

    The Cases API REPLACES the whole encrypted blob — callers MUST merge
    with ``get_agency_storage()`` first (handlers_admin owns that merge;
    an empty submitted secret means keep-existing). Body shape:
    ``{"storage": {"backend": "nextcloud", "nextcloud": {url, username,
    password, base_path}}, "database"?: {...}, "updated_by"?: str}``.
    """
    return await _put(f"/agency/{agency_id}/storage", body)
