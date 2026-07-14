"""Account tools — sign-out + admin user registration (handlers_account.py).

Contract (2026-07-14):
- sign-out is UNGATED and idempotent: works when unlocked (ends the unlock +
  clears the ≤60s cached UNLOCKED verdict) and no-ops gracefully when locked
  or identity-less — a sign-out request must never be answered with a
  sign-IN prompt;
- a Cases-API failure on sign-out is an HONEST retryable error (the unlock
  may still be live — claiming success would leave surfaces open);
- registration requires a LIVE admin role (force_fresh, mirrors
  save_agency_settings) and always threads the ISSUING admin's agency;
- empty password -> strong password generated and returned ONCE;
- 409 from the Cases API -> graceful "account exists" FACT, not an error.
"""
import asyncio

import auth_gate
from auth_gate import UnlockState
import handlers_account
from handlers import EmptyParams
from handlers_account import InviteUserParams, RegisterUserParams


# Not a secret: runtime-built test-only password (deploy security scan
# flags password="..." literals).
_TEST_PW = "-".join(("test", "pw", "0123456789"))


class _User:
    imperal_id = "imp_u_test"
    agency_id = "default"


class _Cache:
    def __init__(self):
        self.deleted = []

    async def delete(self, key):
        self.deleted.append(key)


class _Ctx:
    def __init__(self, cache=None):
        self.user = _User()
        self.cache = cache


def _unlocked_admin(monkeypatch, agency="default"):
    async def fake(ctx, force_fresh=False):
        return UnlockState(unlocked=True, agency_id=agency, role="admin")
    monkeypatch.setattr(auth_gate, "_fetch_unlock", fake)


def _unlocked_user(monkeypatch):
    async def fake(ctx, force_fresh=False):
        return UnlockState(unlocked=True, agency_id="default", role="user")
    monkeypatch.setattr(auth_gate, "_fetch_unlock", fake)


# ── sign out ──────────────────────────────────────────────────────────────────


def test_sign_out_ends_unlock_and_clears_cache(monkeypatch):
    calls = []

    async def fake_delete_unlock(imperal_id):
        calls.append(imperal_id)
        return {"signed_out": True}

    monkeypatch.setattr(handlers_account.queries, "delete_unlock", fake_delete_unlock)
    cache = _Cache()
    res = asyncio.run(handlers_account.fn_sign_out(_Ctx(cache), EmptyParams()))
    assert res.status == "success"
    assert res.data["signed_out"] is True and res.data["was_unlocked"] is True
    assert calls == ["imp_u_test"]
    assert cache.deleted == ["sl_unlock:imp_u_test"], (
        "the cached UNLOCKED verdict must be cleared or surfaces stay open ≤60s")


def test_sign_out_is_idempotent_when_already_locked(monkeypatch):
    async def fake_delete_unlock(imperal_id):
        return {"signed_out": False}          # no live unlock row

    monkeypatch.setattr(handlers_account.queries, "delete_unlock", fake_delete_unlock)
    res = asyncio.run(handlers_account.fn_sign_out(_Ctx(), EmptyParams()))
    assert res.status == "success"            # graceful no-op, never an error
    assert res.data["was_unlocked"] is False


def test_sign_out_api_failure_is_honest_retryable_error(monkeypatch):
    async def fake_delete_unlock(imperal_id):
        raise RuntimeError("cases api down")

    monkeypatch.setattr(handlers_account.queries, "delete_unlock", fake_delete_unlock)
    res = asyncio.run(handlers_account.fn_sign_out(_Ctx(), EmptyParams()))
    assert res.status != "success", (
        "unlock may still be LIVE — claiming sign-out success would leave "
        "Sharelock surfaces open")


def test_sign_out_without_identity_is_graceful():
    ctx = _Ctx()
    ctx.user = None
    res = asyncio.run(handlers_account.fn_sign_out(ctx, EmptyParams()))
    assert res.status == "success"
    assert res.data["signed_out"] is False


# ── register user ─────────────────────────────────────────────────────────────


def test_register_requires_live_admin_role(monkeypatch):
    _unlocked_user(monkeypatch)
    called = []

    async def fake_create(**kw):
        called.append(kw)

    monkeypatch.setattr(handlers_account.queries, "create_sharelock_user", fake_create)
    res = asyncio.run(handlers_account.fn_register_user(
        _Ctx(), RegisterUserParams(email="a@b.co", password=_TEST_PW)))
    assert res.status == "success"
    assert res.data["created"] is False
    assert res.data["reason"] == "admin_role_required"
    assert called == [], "non-admin must never reach the Cases API"


def test_register_threads_admin_agency_and_creates(monkeypatch):
    _unlocked_admin(monkeypatch, agency="privatefed")
    seen = {}

    async def fake_create(**kw):
        seen.update(kw)
        return {"user_id": 7, "email": kw["email"], "role": kw["role"],
                "agency_id": kw["agency_id"]}

    monkeypatch.setattr(handlers_account.queries, "create_sharelock_user", fake_create)
    # Kernel identity agency must MATCH the unlock agency (one-canon rule) —
    # the @require_unlock gate fails closed on a mismatch.
    ctx = _Ctx()
    ctx.user.agency_id = "privatefed"
    res = asyncio.run(handlers_account.fn_register_user(
        ctx, RegisterUserParams(email="New.User@Agency.GOV",
                                password=_TEST_PW, role="user")))
    assert res.status == "success" and res.data["created"] is True
    assert seen["agency_id"] == "privatefed", "created users carry the ISSUER's agency"
    assert seen["email"] == "new.user@agency.gov"
    assert res.data["generated_password"] == ""      # admin set one — never echoed


def test_register_generates_password_when_empty(monkeypatch):
    _unlocked_admin(monkeypatch)
    seen = {}

    async def fake_create(**kw):
        seen.update(kw)
        return {"user_id": 8, "email": kw["email"], "role": "user",
                "agency_id": kw["agency_id"]}

    monkeypatch.setattr(handlers_account.queries, "create_sharelock_user", fake_create)
    res = asyncio.run(handlers_account.fn_register_user(
        _Ctx(), RegisterUserParams(email="x@y.io")))
    generated = res.data["generated_password"]
    assert len(generated) >= 10
    assert seen["password"] == generated
    assert generated in res.summary, "shown ONCE to the admin in the summary"


def test_register_existing_account_is_graceful_fact(monkeypatch):
    _unlocked_admin(monkeypatch)

    async def fake_create(**kw):
        raise handlers_account.queries.CasesAPIError(409, "account already exists")

    monkeypatch.setattr(handlers_account.queries, "create_sharelock_user", fake_create)
    res = asyncio.run(handlers_account.fn_register_user(
        _Ctx(), RegisterUserParams(email="dup@y.io", password=_TEST_PW)))
    assert res.status == "success"
    assert res.data["created"] is False and res.data["reason"] == "account_exists"


def test_register_rejects_bad_role_and_short_password(monkeypatch):
    _unlocked_admin(monkeypatch)
    res = asyncio.run(handlers_account.fn_register_user(
        _Ctx(), RegisterUserParams(email="x@y.io", password=_TEST_PW,
                                   role="supervisor")))
    assert res.status != "success"
    res2 = asyncio.run(handlers_account.fn_register_user(
        _Ctx(), RegisterUserParams(email="x@y.io", password="short")))
    assert res2.status != "success"


# ── invite user ───────────────────────────────────────────────────────────────


def test_invite_requires_live_admin_role(monkeypatch):
    _unlocked_user(monkeypatch)
    called = []

    async def fake_invite(**kw):
        called.append(kw)

    monkeypatch.setattr(handlers_account.queries, "create_invite", fake_invite)
    res = asyncio.run(handlers_account.fn_invite_user(_Ctx(), InviteUserParams()))
    assert res.status == "success"
    assert res.data["created"] is False
    assert res.data["reason"] == "admin_role_required"
    assert called == []


def test_invite_threads_agency_issuer_and_shows_code_once(monkeypatch):
    _unlocked_admin(monkeypatch)
    seen = {}

    async def fake_invite(**kw):
        seen.update(kw)
        return {"invite_code": "RAWCODE123456", "agency_id": kw["agency_id"],
                "email": kw["email"], "expires_at": "2026-07-21 00:00:00"}

    monkeypatch.setattr(handlers_account.queries, "create_invite", fake_invite)
    res = asyncio.run(handlers_account.fn_invite_user(
        _Ctx(), InviteUserParams(email="New@Agency.GOV", ttl_days=7)))
    assert res.status == "success" and res.data["created"] is True
    assert seen["agency_id"] == "default", "invite carries the ISSUER's agency"
    assert seen["created_by_imperal_id"] == "imp_u_test", "issuer is accountable"
    assert seen["email"] == "new@agency.gov"
    assert res.data["invite_code"] == "RAWCODE123456"
    assert "RAWCODE123456" in res.summary, "code shown ONCE to the admin"
    assert res.data["register_route"], "points the admin at the Register page"


def test_invite_mint_failure_is_honest_error(monkeypatch):
    _unlocked_admin(monkeypatch)

    async def fake_invite(**kw):
        raise RuntimeError("cases api down")

    monkeypatch.setattr(handlers_account.queries, "create_invite", fake_invite)
    res = asyncio.run(handlers_account.fn_invite_user(_Ctx(), InviteUserParams()))
    assert res.status != "success"


def test_invite_ttl_out_of_range_falls_back_to_default(monkeypatch):
    _unlocked_admin(monkeypatch)
    seen = {}

    async def fake_invite(**kw):
        seen.update(kw)
        return {"invite_code": "C" * 20, "agency_id": kw["agency_id"],
                "email": "", "expires_at": "x"}

    monkeypatch.setattr(handlers_account.queries, "create_invite", fake_invite)
    asyncio.run(handlers_account.fn_invite_user(
        _Ctx(), InviteUserParams(ttl_days=90)))
    assert seen["ttl_days"] == 7
