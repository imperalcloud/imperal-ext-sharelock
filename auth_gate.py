"""Sharelock v2 — @require_unlock gate (Track A login, 2026-06-11).

Sharelock requires its own sign-in ON TOP of the Imperal session: the panel
form (``/ext/sharelock-v2/signin``) verifies credentials against
sharelock-icnli and upserts an unlock record keyed by ``imperal_id``; the
Cases API exposes it via the service-key-gated
``GET /auth/unlock/{imperal_id}`` (read through ``queries.get_unlock`` — the
single Cases-API HTTP door).

Without a live unlock every Sharelock surface (15 chat tools, the skeleton,
both panels) returns a graceful typed "sign in" FACT — a SUCCESS result
carrying the ``LockedState`` SDL entity. Never an error result, never raw
case data; the narrator owns language/phrasing (ICNLI), panels render a
sign-in placeholder.

Fail-closed: if the unlock state cannot be read (Cases API degraded) the
surface stays locked — forensic product, confidentiality beats availability.
"""
import functools
import logging

from pydantic import BaseModel, Field

from imperal_sdk import sdl, ui
from imperal_sdk.chat import ActionResult

from app import ext
import queries

log = logging.getLogger("sharelock-v2.auth_gate")

PANEL_ROUTE = "/ext/sharelock-v2/signin"
REGISTER_ROUTE = "/ext/sharelock-v2/register"
# ctx.cache enforces TTL within [5, 300]. Unlocked verdicts are stable for a
# minute; LOCKED verdicts must expire fast so a fresh panel sign-in unlocks
# chat/panels within seconds, not a minute.
_CACHE_TTL_UNLOCKED = 60
_CACHE_TTL_LOCKED = 10


@ext.cache_model("sharelock_unlock")
class UnlockState(BaseModel):
    """Per-user cached unlock state (mirrors GET /auth/unlock/{imperal_id})."""
    unlocked: bool = False
    agency_id: str = "default"
    role: str = "user"


class LockedState(sdl.Entity):
    """SDL fact: no live Sharelock unlock for this user — sign-in required."""
    id: str = "sharelock-signin"
    title: str = "Sharelock sign-in required"
    kind: str = "auth_lock"
    unlocked: bool = False
    reason: str = "sharelock_signin_required"
    signin_methods: list[str] = Field(default_factory=lambda: ["password", "magic_link"])
    panel_route: str = PANEL_ROUTE


async def _fetch_unlock(ctx) -> UnlockState:
    """Live unlock state for ``ctx.user`` (cached via ``ctx.cache``).

    Asymmetric TTL: unlocked verdicts cache ≤60s; LOCKED verdicts cache
    ≤10s (so a fresh panel sign-in takes effect within seconds). Any
    cached verdict is fresh by construction of the write-side TTL.

    No identity, read error, or cache failure all resolve to LOCKED.
    """
    user = getattr(ctx, "user", None)
    imperal_id = str(getattr(user, "imperal_id", "") or "") if user else ""
    if not imperal_id:
        return UnlockState(unlocked=False)

    async def _fetch() -> UnlockState:
        data = await queries.get_unlock(imperal_id)
        return UnlockState(
            unlocked=bool(data.get("unlocked")),
            agency_id=str(data.get("agency_id") or "default"),
            role=str(data.get("role") or "user"),
        )

    # ctx.cache is a PROPERTY that may raise when the Context was built
    # without cache plumbing (test harnesses, degraded extcache) — a cache
    # problem must degrade to a direct read, never decide the lock.
    try:
        cache = getattr(ctx, "cache", None)
    except Exception:
        cache = None

    key = f"sl_unlock:{imperal_id}"
    if cache is not None:
        try:
            cached = await cache.get(key, UnlockState)
            if cached is not None:
                return cached
        except Exception:
            cache = None  # cache layer degraded — the direct read decides

    try:
        state = await _fetch()
    except Exception as e:
        log.warning(f"unlock read failed (fail-closed): {e}")
        return UnlockState(unlocked=False)

    if cache is not None:
        try:
            await cache.set(
                key, state,
                ttl_seconds=(_CACHE_TTL_UNLOCKED if state.unlocked
                             else _CACHE_TTL_LOCKED),
            )
        except Exception:
            pass  # cache write failure must never affect the verdict
    return state


def locked_fact() -> dict:
    """The locked-state SDL fact as a plain dict (skeleton/panels reuse it)."""
    return LockedState().model_dump()


def locked_result() -> ActionResult:
    return ActionResult.success(
        data=locked_fact(),
        summary=(
            "Sharelock sign-in required — open the Sharelock panel "
            f"({PANEL_ROUTE}) and sign in with your Sharelock account."
        ),
    )


def locked_panel():
    """Sign-in placeholder for panel surfaces (sidebar/dashboard)."""
    return ui.Stack(children=[
        ui.Text("Sharelock is locked"),
        ui.Text("Sign in with your Sharelock account to access cases."),
        ui.Button("Sign in to Sharelock", variant="primary", icon="lock",
                  on_click=ui.Navigate(PANEL_ROUTE)),
        ui.Button("Register with invite code", variant="ghost", icon="user-plus",
                  on_click=ui.Navigate(REGISTER_ROUTE)),
    ])


def require_unlock(fn):
    """Gate a ``@chat.function`` handler behind the Sharelock unlock.

    Placement: between ``@chat.function(...)`` and the ``async def`` —
    ``functools.wraps`` copies ``__annotations__`` and sets ``__wrapped__``,
    so the SDK's params-model detection (``typing.get_type_hints`` +
    ``inspect.signature``) keeps seeing the real handler signature.
    """
    @functools.wraps(fn)
    async def wrapper(ctx, *args, **kwargs):
        state = await _fetch_unlock(ctx)
        if not state.unlocked:
            return locked_result()
        return await fn(ctx, *args, **kwargs)
    return wrapper
