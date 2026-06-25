"""Gate agency cross-check (multi-agency seam, Track C final-review IMPORTANT-1).

An unlock record minted under one agency must NOT open surfaces for an
identity the kernel attributes to another agency:
- ``require_unlock``: unlocked=True but unlock.agency_id != kernel agency
  → treated as LOCKED (typed fact), body never runs, warning logged;
- ``unlock_ok``: the combined fetch+consistency check skeleton/panels use;
- today all agencies are "default" → zero behavior change (locked in here).
"""
import asyncio
import logging

import auth_gate
from auth_gate import UnlockState, _agency_consistent, require_unlock, unlock_ok


class _User:
    def __init__(self, agency_id="default"):
        self.imperal_id = "imp_u_test"
        self.agency_id = agency_id


class _Ctx:
    def __init__(self, agency_id="default"):
        self.user = _User(agency_id)
        self.cache = None  # no CacheClient in tests — direct fetch path


def _patch_state(monkeypatch, **kwargs):
    async def fake(ctx, force_fresh=False):
        return UnlockState(**kwargs)
    monkeypatch.setattr(auth_gate, "_fetch_unlock", fake)


# ── _agency_consistent helper ─────────────────────────────────────────────────


def test_agency_consistent_on_match():
    state = UnlockState(unlocked=True, agency_id="agency-x")
    assert _agency_consistent(_Ctx("agency-x"), state) is True


def test_agency_consistent_default_fallback_for_legacy_identity():
    """ctx.user.agency_id None/empty collapses to 'default' (rollout rows)."""
    state = UnlockState(unlocked=True, agency_id="default")
    assert _agency_consistent(_Ctx(None), state) is True
    assert _agency_consistent(_Ctx(""), state) is True


def test_agency_mismatch_fails_and_logs_one_canon_warning(caplog):
    state = UnlockState(unlocked=True, agency_id="agency-x")
    with caplog.at_level(logging.WARNING, logger="sharelock-v2.auth_gate"):
        assert _agency_consistent(_Ctx("default"), state) is False
    assert any("unlock agency mismatch" in r.getMessage()
               and "failing closed" in r.getMessage()
               for r in caplog.records), caplog.records


# ── require_unlock gate ───────────────────────────────────────────────────────


def test_gate_agency_mismatch_treated_as_locked(monkeypatch):
    calls = []

    @require_unlock
    async def tool(ctx, message: str = ""):
        calls.append(message)
        return {"ok": True}

    _patch_state(monkeypatch, unlocked=True, agency_id="agency-x", role="admin")
    res = asyncio.run(tool(_Ctx("default"), message="hi"))

    assert calls == [], "tool body must NOT run on agency mismatch"
    assert res.status == "success", "mismatch is the locked FACT, never an error"
    assert res.data["reason"] == "sharelock_signin_required"
    assert res.data["unlocked"] is False


def test_gate_agency_match_passes_through(monkeypatch):
    @require_unlock
    async def tool(ctx, message: str = ""):
        return {"ok": True, "echo": message}

    _patch_state(monkeypatch, unlocked=True, agency_id="agency-x", role="user")
    res = asyncio.run(tool(_Ctx("agency-x"), message="hi"))
    assert res == {"ok": True, "echo": "hi"}


# ── unlock_ok (skeleton / panel surface check) ────────────────────────────────


def test_unlock_ok_true_when_unlocked_and_consistent(monkeypatch):
    _patch_state(monkeypatch, unlocked=True, agency_id="default")
    assert asyncio.run(unlock_ok(_Ctx("default"))) is True


def test_unlock_ok_false_when_locked(monkeypatch):
    _patch_state(monkeypatch, unlocked=False, agency_id="default")
    assert asyncio.run(unlock_ok(_Ctx("default"))) is False


def test_unlock_ok_false_on_agency_mismatch(monkeypatch):
    _patch_state(monkeypatch, unlocked=True, agency_id="agency-x")
    assert asyncio.run(unlock_ok(_Ctx("default"))) is False
