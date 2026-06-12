"""Track A login — @require_unlock gate (auth_gate.py).

Contract (plan 2026-06-11-sharelock-trackA-login.md, Task A5):
- locked surface returns ActionResult.success with the typed LockedState
  SDL fact (reason=sharelock_signin_required, panel_route) — NEVER an
  error result, NEVER raw case data;
- unlocked surface passes through to the tool body untouched;
- unlock-state read fails CLOSED (Cases API degraded → locked);
- the decorator preserves introspection (chat.function reads the params
  model from the wrapped signature/annotations);
- all 30 @chat.function tools + skeleton + both panels are gated
  (10 core + share/unshare/list-shares/upload/save-settings (C.2 T3) +
  15 Track-D control/drill-down tools);
- locked verdicts cache ≤10s (unlocked ≤60s) so a fresh panel sign-in
  takes effect within seconds.
"""
import asyncio
import inspect
import os
import re
import typing

import auth_gate
from auth_gate import LockedState, UnlockState, require_unlock

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class _User:
    imperal_id = "imp_u_test"
    agency_id = "default"


class _Ctx:
    def __init__(self):
        self.user = _User()
        self.cache = None  # no CacheClient in tests — direct fetch path


def _locked(monkeypatch):
    async def fake(ctx):
        return UnlockState(unlocked=False)
    monkeypatch.setattr(auth_gate, "_fetch_unlock", fake)


def _unlocked(monkeypatch):
    # agency_id matches _User.agency_id — the gate also cross-checks the
    # unlock row's agency against the kernel identity (one-canon rule).
    async def fake(ctx):
        return UnlockState(unlocked=True, agency_id="default", role="admin")
    monkeypatch.setattr(auth_gate, "_fetch_unlock", fake)


def test_locked_returns_success_fact_not_error(monkeypatch):
    calls = []

    @require_unlock
    async def tool(ctx, message: str = ""):
        calls.append(message)
        return {"ok": True}

    _locked(monkeypatch)
    res = asyncio.run(tool(_Ctx(), message="hi"))

    assert calls == [], "tool body must NOT run when locked"
    assert res.status == "success", "locked state is a FACT, never an error"
    assert res.error is None
    data = res.data
    assert data["unlocked"] is False
    assert data["reason"] == "sharelock_signin_required"
    assert data["panel_route"] == "/ext/sharelock-v2/signin"
    assert data["kind"] == "auth_lock"
    assert res.summary  # narrator renders language — fact carries a summary


def test_unlocked_passes_through(monkeypatch):
    @require_unlock
    async def tool(ctx, message: str = ""):
        return {"ok": True, "echo": message}

    _unlocked(monkeypatch)
    res = asyncio.run(tool(_Ctx(), message="hi"))
    assert res == {"ok": True, "echo": "hi"}


def test_fetch_unlock_fails_closed(monkeypatch):
    async def boom(imperal_id):
        raise RuntimeError("cases api down")
    monkeypatch.setattr(auth_gate.queries, "get_unlock", boom)

    state = asyncio.run(auth_gate._fetch_unlock(_Ctx()))
    assert state.unlocked is False, "unreadable unlock state must lock, not open"


def test_cache_failure_degrades_to_direct_read(monkeypatch):
    """A broken/raising ctx.cache must NOT decide the lock — direct read wins."""
    async def ok(imperal_id):
        return {"unlocked": True, "agency_id": "agency-y", "role": "user"}
    monkeypatch.setattr(auth_gate.queries, "get_unlock", ok)

    class _RaisingCacheCtx:
        user = _User()

        @property
        def cache(self):
            raise RuntimeError("ctx.cache is not available in this context")

    state = asyncio.run(auth_gate._fetch_unlock(_RaisingCacheCtx()))
    assert state.unlocked is True
    assert state.agency_id == "agency-y"


def test_fetch_unlock_requires_identity():
    class _NoUser:
        user = None
        cache = None
    state = asyncio.run(auth_gate._fetch_unlock(_NoUser()))
    assert state.unlocked is False


def test_decorator_preserves_introspection():
    class Params:  # stand-in for the pydantic *Params model
        pass

    async def tool(ctx, params: Params):
        return None

    wrapped = require_unlock(tool)
    assert wrapped.__name__ == "tool"
    # chat.function reads annotations via typing.get_type_hints(func)
    hints = typing.get_type_hints(wrapped)
    assert hints.get("params") is Params
    # and the params model via inspect.signature (follows __wrapped__)
    assert "params" in inspect.signature(wrapped).parameters


def test_locked_state_is_sdl_entity():
    fact = LockedState().model_dump()
    assert fact["id"] and fact["title"] and fact["kind"] == "auth_lock"
    assert fact["unlocked"] is False
    assert "password" in fact["signin_methods"]
    schema = LockedState.model_json_schema()
    assert schema.get("x-sdl") == "entity"


def _src(name: str) -> str:
    with open(os.path.join(_ROOT, name)) as f:
        return f.read()


def test_all_chat_tools_gated():
    """Every @chat.function block must carry @require_unlock before its def."""
    gated = 0
    for fname in ("handlers.py", "handlers_analysis.py",
                  "handlers_share.py", "handlers_files.py",
                  "handlers_admin.py",
                  # Track D (D2 report / D3 deletes / D4 drill-down)
                  "handlers_control.py", "handlers_drilldown.py",
                  "handlers_intel.py"):
        src = _src(fname)
        for m in re.finditer(r"@chat\.function\(", src):
            seg = src[m.start(): src.index("async def", m.start())]
            assert "@require_unlock" in seg, (
                f"{fname}: @chat.function at offset {m.start()} is not gated"
            )
            gated += 1
    # 15 original + 15 Track-D (get_report; delete_case/delete_file;
    # list_case_files/get_case_detail/update_case/analysis_status/
    # get_intelligence_graph/list_entities/get_entity/list_relationships/
    # list_timeline_events/get_taxonomy/get_audit_log/list_analysis_runs)
    assert gated == 30, f"expected 30 gated chat tools, found {gated}"


def test_skeleton_and_panels_gated():
    # skeleton + sidebar use the combined unlock_ok (fetch + agency check);
    # the dashboard panel needs role, so it fetches state and checks both.
    assert "unlock_ok" in _src("skeleton.py"), "skeleton must check unlock"
    assert "unlock_ok" in _src("panels.py"), "sidebar panel must check unlock"
    src_case = _src("panels_case.py")
    assert "_fetch_unlock" in src_case, "dashboard panel must check unlock"
    assert "_agency_consistent" in src_case, \
        "dashboard panel must cross-check unlock agency vs kernel agency"


def test_no_error_result_and_no_ui_dict_in_gate():
    src = _src("auth_gate.py")
    assert "ActionResult.error" not in src, "locked state must never be an error"
    assert '"ui"' not in src and "'ui'" not in src, "chat tools must not return ui dicts"


# ── Locked-state cache TTL (UI batch, Track C.2 T3) ──────────────────────────


class _RecordingCache:
    """ctx.cache stand-in that records set() TTLs and replays get()."""

    def __init__(self):
        self.sets = []
        self.stored = None

    async def get(self, key, model):
        return self.stored

    async def set(self, key, value, ttl_seconds=60):
        self.sets.append((key, value, ttl_seconds))


class _CacheCtx:
    def __init__(self, cache):
        self.user = _User()
        self.cache = cache


def test_locked_verdict_cached_short(monkeypatch):
    """Asymmetric TTL: unlocked verdicts cache 60s, LOCKED verdicts 10s —
    a fresh panel sign-in must take effect within seconds."""
    cache = _RecordingCache()

    async def locked(imperal_id):
        return {"unlocked": False}
    monkeypatch.setattr(auth_gate.queries, "get_unlock", locked)
    state = asyncio.run(auth_gate._fetch_unlock(_CacheCtx(cache)))
    assert state.unlocked is False
    assert cache.sets and cache.sets[-1][2] == 10, (
        f"locked verdict must cache <=10s, got {cache.sets}")

    async def unlocked(imperal_id):
        return {"unlocked": True, "agency_id": "agency-x", "role": "admin"}
    monkeypatch.setattr(auth_gate.queries, "get_unlock", unlocked)
    state = asyncio.run(auth_gate._fetch_unlock(_CacheCtx(cache)))
    assert state.unlocked is True
    assert cache.sets[-1][2] == 60, (
        f"unlocked verdict must cache 60s, got {cache.sets[-1]}")


def test_cached_verdict_short_circuits_fetch(monkeypatch):
    """A cached verdict (locked or not) is returned without re-fetching —
    freshness is guaranteed by the write-side TTL."""
    cache = _RecordingCache()
    cache.stored = UnlockState(unlocked=False)

    async def boom(imperal_id):
        raise AssertionError("must not fetch when the verdict is cached")
    monkeypatch.setattr(auth_gate.queries, "get_unlock", boom)

    state = asyncio.run(auth_gate._fetch_unlock(_CacheCtx(cache)))
    assert state.unlocked is False
    assert cache.sets == [], "cached verdict must not be re-written"


def test_fetch_failure_skips_cache_write(monkeypatch):
    """Fail-closed lock from a failed fetch must NOT be cached — the next
    render retries the read instead of pinning a degraded verdict."""
    cache = _RecordingCache()

    async def boom(imperal_id):
        raise RuntimeError("cases api down")
    monkeypatch.setattr(auth_gate.queries, "get_unlock", boom)

    state = asyncio.run(auth_gate._fetch_unlock(_CacheCtx(cache)))
    assert state.unlocked is False
    assert cache.sets == []


# ── Locked panel (sign-in placeholder with action buttons) ───────────────────


def test_locked_panel_renders_signin_buttons():
    node = auth_gate.locked_panel().to_dict()
    buttons = [c for c in node["props"]["children"]
               if c.get("type") == "Button"]
    assert len(buttons) == 2, f"expected 2 buttons, got {node}"
    signin, register = buttons
    assert signin["props"]["label"] == "Sign in to Sharelock"
    assert signin["props"]["on_click"]["action"] == "navigate"
    assert signin["props"]["on_click"]["path"] == auth_gate.PANEL_ROUTE
    assert register["props"]["on_click"]["action"] == "navigate"
    assert register["props"]["on_click"]["path"] == auth_gate.REGISTER_ROUTE
