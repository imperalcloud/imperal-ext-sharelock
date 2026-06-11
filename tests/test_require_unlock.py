"""Track A login — @require_unlock gate (auth_gate.py).

Contract (plan 2026-06-11-sharelock-trackA-login.md, Task A5):
- locked surface returns ActionResult.success with the typed LockedState
  SDL fact (reason=sharelock_signin_required, panel_route) — NEVER an
  error result, NEVER raw case data;
- unlocked surface passes through to the tool body untouched;
- unlock-state read fails CLOSED (Cases API degraded → locked);
- the decorator preserves introspection (chat.function reads the params
  model from the wrapped signature/annotations);
- all 10 @chat.function tools + skeleton + both panels are gated.
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
    async def fake(ctx):
        return UnlockState(unlocked=True, agency_id="agency-x", role="admin")
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
    for fname in ("handlers.py", "handlers_analysis.py"):
        src = _src(fname)
        for m in re.finditer(r"@chat\.function\(", src):
            seg = src[m.start(): src.index("async def", m.start())]
            assert "@require_unlock" in seg, (
                f"{fname}: @chat.function at offset {m.start()} is not gated"
            )
            gated += 1
    assert gated == 10, f"expected 10 gated chat tools, found {gated}"


def test_skeleton_and_panels_gated():
    assert "_fetch_unlock" in _src("skeleton.py"), "skeleton must check unlock"
    assert "_fetch_unlock" in _src("panels.py"), "sidebar panel must check unlock"
    assert "_fetch_unlock" in _src("panels_case.py"), "dashboard panel must check unlock"


def test_no_error_result_and_no_ui_dict_in_gate():
    src = _src("auth_gate.py")
    assert "ActionResult.error" not in src, "locked state must never be an error"
    assert '"ui"' not in src and "'ui'" not in src, "chat tools must not return ui dicts"
