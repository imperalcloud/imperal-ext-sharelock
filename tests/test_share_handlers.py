"""Track C.2 T3 — share machinery handlers (handlers_share.py).

Contract:
- share_case passes `colleague` to the Cases API AS-IS as the grant's
  imperal_id (no ext-side email resolution today) and says so HONESTLY in
  the fact (`note`) when the input doesn't look like an imperal id;
- granted_by = the caller's imperal_id (X-Imperal-User-ID header side);
- unshare_case maps the API's deleted-rowcount to an honest unshared flag;
- list_case_shares returns a real SDL entity-list shape (items=raw rows).
All reads/writes thread agency_id.
"""
import asyncio

import auth_gate
import handlers_share as hs
from handlers_share import (
    ShareCaseParams, UnshareCaseParams, CaseSharesParams,
)


class _User:
    imperal_id = "imp_u_owner"
    agency_id = "acme"


class _Ctx:
    user = _User()
    cache = None


def _unlocked(monkeypatch):
    async def fake(ctx, force_fresh=False):
        return auth_gate.UnlockState(unlocked=True, agency_id="acme", role="user")
    monkeypatch.setattr(auth_gate, "_fetch_unlock", fake)


def _case_ok(monkeypatch):
    async def get_case(case_id, agency_id=None):
        return {"id": case_id, "name": "Case X"}
    monkeypatch.setattr(hs.queries, "get_case", get_case)


def test_share_case_passes_colleague_verbatim(monkeypatch):
    _unlocked(monkeypatch)
    _case_ok(monkeypatch)
    seen = {}

    async def post_share(case_id, imperal_id, granted_by, agency_id=None):
        seen.update(case_id=case_id, imperal_id=imperal_id,
                    granted_by=granted_by, agency_id=agency_id)
        return {"ok": True, "case_id": case_id, "imperal_id": imperal_id}
    monkeypatch.setattr(hs.queries, "post_share", post_share)

    res = asyncio.run(hs.fn_share_case(
        _Ctx(), ShareCaseParams(case_id=5, colleague="imp_u_colleague")))
    assert res.status == "success"
    assert seen == {"case_id": 5, "imperal_id": "imp_u_colleague",
                    "granted_by": "imp_u_owner", "agency_id": "acme"}
    assert res.data["shared"] is True
    assert res.data["imperal_id"] == "imp_u_colleague"
    assert res.data["note"] is None  # imp_-prefixed: no honesty note needed


def test_share_case_non_imp_input_stored_verbatim_with_honest_note(monkeypatch):
    _unlocked(monkeypatch)
    _case_ok(monkeypatch)
    seen = {}

    async def post_share(case_id, imperal_id, granted_by, agency_id=None):
        seen["imperal_id"] = imperal_id
        return {"ok": True}
    monkeypatch.setattr(hs.queries, "post_share", post_share)

    res = asyncio.run(hs.fn_share_case(
        _Ctx(), ShareCaseParams(case_id=5, colleague="bob@example.com")))
    assert res.status == "success"
    assert seen["imperal_id"] == "bob@example.com", "input must ride AS-IS"
    assert res.data["note"], "non-imp_ identifier must carry the honesty note"
    assert "verbatim" in res.data["note"]


def test_share_case_unknown_case_is_error(monkeypatch):
    _unlocked(monkeypatch)

    async def get_case(case_id, agency_id=None):
        return {}  # Cases API 404 -> {}
    monkeypatch.setattr(hs.queries, "get_case", get_case)

    res = asyncio.run(hs.fn_share_case(
        _Ctx(), ShareCaseParams(case_id=999, colleague="imp_u_x")))
    assert res.status == "error"
    assert "not found" in res.error


def test_unshare_case_deleted_and_noop(monkeypatch):
    _unlocked(monkeypatch)

    async def delete_share(case_id, imperal_id, agency_id=None):
        return {"ok": True, "deleted": 1 if imperal_id == "imp_u_x" else 0}
    monkeypatch.setattr(hs.queries, "delete_share", delete_share)

    res = asyncio.run(hs.fn_unshare_case(
        _Ctx(), UnshareCaseParams(case_id=5, colleague="imp_u_x")))
    assert res.status == "success"
    assert res.data == {"unshared": True, "deleted": 1,
                        "case_id": 5, "imperal_id": "imp_u_x"}

    res = asyncio.run(hs.fn_unshare_case(
        _Ctx(), UnshareCaseParams(case_id=5, colleague="imp_u_gone")))
    assert res.status == "success"
    assert res.data["unshared"] is False and res.data["deleted"] == 0
    assert "nothing to revoke" in res.summary


def test_list_case_shares_sdl_entity_list_shape(monkeypatch):
    _unlocked(monkeypatch)
    shares = [
        {"imperal_id": "imp_u_a", "granted_by": "imp_u_owner",
         "created_at": "2026-06-12T00:00:00", "email": "a@x.com", "name": "A"},
        {"imperal_id": "imp_u_b", "granted_by": "imp_u_owner",
         "created_at": "2026-06-12T00:00:01", "email": None, "name": None},
    ]
    owner = {"imperal_id": "imp_u_owner", "email": "o@x.com", "name": "Owner"}

    async def get_shares(case_id, agency_id=None):
        assert agency_id == "acme"
        return {"case_id": case_id, "owner": owner, "shares": shares}
    monkeypatch.setattr(hs.queries, "get_shares", get_shares)

    res = asyncio.run(hs.fn_list_case_shares(_Ctx(), CaseSharesParams(case_id=5)))
    assert res.status == "success"
    assert res.data["items"] == shares  # raw rows; data_model derives id/title
    assert res.data["case_id"] == 5
    assert res.data["owner"] == owner
    assert "o@x.com" in res.summary and "imp_u_b" in res.summary


def test_share_tools_locked_fact_when_locked(monkeypatch):
    async def fake(ctx, force_fresh=False):
        return auth_gate.UnlockState(unlocked=False)
    monkeypatch.setattr(auth_gate, "_fetch_unlock", fake)

    res = asyncio.run(hs.fn_share_case(
        _Ctx(), ShareCaseParams(case_id=5, colleague="imp_u_x")))
    assert res.status == "success"
    assert res.data["reason"] == "sharelock_signin_required"
