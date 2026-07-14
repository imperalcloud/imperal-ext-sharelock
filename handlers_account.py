"""Sharelock v2 — account chat tools: sign-out + admin user registration.

Sharelock keeps its OWN user store (Cases API ``users`` table) on top of the
Imperal session — signing in mints an unlock row (auth_gate). This module
closes the account lifecycle:

* ``sharelock_sign_out`` — EVERY signed-in user can end their unlock early
  (the 8h/30d window otherwise just expires). Idempotent, never gated: a
  locked user signing out is a graceful no-op, and it also clears the ≤60s
  cached UNLOCKED verdict so the lock takes effect on the very next check.
* ``sharelock_register_user`` — a Sharelock ADMIN creates an account in the
  agency's user store directly (complements the invite flow). Live admin
  check (force_fresh — never the 60s cache, mirrors save_agency_settings);
  the created user always carries the ISSUING admin's agency (invite rule).
"""
import logging
import secrets

from pydantic import BaseModel, Field

from imperal_sdk.chat import ActionResult

from app import chat
import auth_gate
import queries
from handlers import EmptyParams

log = logging.getLogger("sharelock-v2.handlers_account")

_GENERATED_PASSWORD_BYTES = 12  # token_urlsafe(12) -> 16 chars, > API min of 10


# ── Data models (typed return contract) ───────────────────────────────────────


class SignOutResult(BaseModel):
    """Outcome of a Sharelock sign-out."""
    signed_out: bool = False
    was_unlocked: bool = False


class RegisterUserResult(BaseModel):
    """Outcome of an admin user registration."""
    created: bool = False
    email: str = ""
    role: str = "user"
    agency_id: str = ""
    generated_password: str = ""
    reason: str = ""


class InviteUserResult(BaseModel):
    """Outcome of minting a registration invite code."""
    created: bool = False
    invite_code: str = ""
    email: str = ""
    agency_id: str = ""
    expires_at: str = ""
    register_route: str = ""
    reason: str = ""


class InviteUserParams(BaseModel):
    email: str = Field("", description=(
        "Optional email hint — when set, ONLY this email can register with "
        "the code. Leave empty for a code anyone can use once."
    ))
    ttl_days: int = Field(7, description=(
        "How many days the code stays valid (1-30, default 7)."
    ))


class RegisterUserParams(BaseModel):
    email: str = Field(..., description=(
        "New user's email — their Sharelock sign-in login. Pass VERBATIM."
    ))
    name: str = Field("", description=(
        "Display name (optional). Pass VERBATIM; empty = derived from email."
    ))
    password: str = Field("", description=(
        "Initial password (min 10 chars). LEAVE EMPTY to auto-generate a "
        "strong one — it is returned ONCE in the result for the admin to "
        "hand to the user."
    ))
    role: str = Field("user", description=(
        "Sharelock role: 'user' (default) or 'admin'."
    ))


# ── Sign out ──────────────────────────────────────────────────────────────────


@chat.function("sharelock_sign_out", action_type="write",
               effects=["update:sharelock_unlock"],
               data_model=SignOutResult,
               description=(
                   "Sign out of Sharelock (end the current Sharelock unlock "
                   "for this user). The Imperal session itself stays signed "
                   "in — only Sharelock locks until the next sign-in."
               ))
async def fn_sign_out(ctx, params: EmptyParams) -> ActionResult:
    """End the caller's unlock. Deliberately NOT @require_unlock-gated:
    sign-out must work (as a no-op) even when already locked, and gating it
    would answer a sign-out request with a sign-IN prompt — nonsense UX."""
    user = getattr(ctx, "user", None)
    imperal_id = str(getattr(user, "imperal_id", "") or "") if user else ""
    if not imperal_id:
        return ActionResult.success(
            data=SignOutResult().model_dump(),
            summary="No Sharelock session to sign out of.",
        )

    try:
        resp = await queries.delete_unlock(imperal_id)
    except Exception as e:
        log.warning(f"sign_out: unlock delete failed for {imperal_id}: {e}")
        return ActionResult.error(
            "Sharelock sign-out did not complete — the Cases service did "
            "not confirm it. Try again.", retryable=True)

    # Clear the ≤60s cached UNLOCKED verdict — without this a fresh sign-out
    # could keep surfaces open until the cache expires. Fail-soft: the row is
    # already expired, the cache self-heals within its TTL.
    try:
        cache = getattr(ctx, "cache", None)
        if cache is not None:
            await cache.delete(f"sl_unlock:{imperal_id}")
    except Exception:
        pass

    was_unlocked = bool(resp.get("signed_out"))
    return ActionResult.success(
        data=SignOutResult(signed_out=True, was_unlocked=was_unlocked).model_dump(),
        summary=("Signed out of Sharelock — all Sharelock surfaces are "
                 "locked until the next sign-in." if was_unlocked else
                 "You were not signed in to Sharelock — nothing to sign out of."),
    )


# ── Admin invite ──────────────────────────────────────────────────────────────


@chat.function("sharelock_invite_user", action_type="write",
               effects=["create:sharelock_invite"],
               data_model=InviteUserResult,
               description=(
                   "Mint a single-use Sharelock registration invite code "
                   "(admin only). Hand the code to the new colleague — they "
                   "register themselves on the Sharelock Register page with "
                   "it, choosing their own password."
               ))
@auth_gate.require_unlock
async def fn_invite_user(ctx, params: InviteUserParams) -> ActionResult:
    """Admin-gated invite minting — the self-service counterpart of
    sharelock_register_user (there the admin sets everything; here the new
    user picks their own password on the Register page)."""
    state = await auth_gate._fetch_unlock(ctx, force_fresh=True)
    if state.role != "admin":
        return ActionResult.success(
            data=InviteUserResult(reason="admin_role_required").model_dump(),
            summary=("Minting Sharelock invites requires the Sharelock "
                     "admin role — ask your agency supervisor."),
        )

    ttl_days = params.ttl_days if 1 <= params.ttl_days <= 30 else 7
    imperal_id = str(getattr(ctx.user, "imperal_id", "") or "")
    try:
        resp = await queries.create_invite(
            agency_id=state.agency_id, created_by_imperal_id=imperal_id,
            email=(params.email or "").strip().lower(), ttl_days=ttl_days,
        )
    except Exception as e:
        log.warning(f"invite_user: mint failed: {e}")
        return ActionResult.error(
            "Invite creation did not complete — the Cases service did not "
            "confirm it. Try again.", retryable=True)

    code = str(resp.get("invite_code") or "")
    if not code:
        return ActionResult.error(
            "Invite creation did not complete — no code returned.",
            retryable=True)
    result = InviteUserResult(
        created=True, invite_code=code,
        email=str(resp.get("email") or ""),
        agency_id=str(resp.get("agency_id") or state.agency_id),
        expires_at=str(resp.get("expires_at") or ""),
        register_route=auth_gate.REGISTER_ROUTE,
    )
    who = f" for {result.email}" if result.email else ""
    return ActionResult.success(
        data=result.model_dump(),
        summary=(f"Invite code{who} (single-use, shown ONCE, valid until "
                 f"{result.expires_at} UTC): {code} — the new user enters it "
                 f"on the Sharelock Register page ({result.register_route}) "
                 "and picks their own password."),
    )


# ── Admin registration ────────────────────────────────────────────────────────


@chat.function("sharelock_register_user", action_type="write",
               effects=["create:sharelock_user"],
               data_model=RegisterUserResult,
               description=(
                   "Register a new Sharelock user account (admin only). "
                   "Creates the account in this agency's Sharelock user "
                   "store; the new user then signs in themselves at the "
                   "Sharelock sign-in. Leave password empty to auto-generate."
               ))
@auth_gate.require_unlock
async def fn_register_user(ctx, params: RegisterUserParams) -> ActionResult:
    """Admin-gated direct account creation (no invite round-trip)."""
    # force_fresh: authz reads the role LIVE (never the 60s unlock cache) —
    # same doctrine as save_agency_settings.
    state = await auth_gate._fetch_unlock(ctx, force_fresh=True)
    if state.role != "admin":
        return ActionResult.success(
            data=RegisterUserResult(reason="admin_role_required").model_dump(),
            summary=("Registering Sharelock users requires the Sharelock "
                     "admin role — ask your agency supervisor."),
        )

    email = (params.email or "").strip().lower()
    if not email or "@" not in email:
        return ActionResult.error("A valid email is required.", retryable=False)
    role = (params.role or "user").strip().lower()
    if role not in ("user", "admin"):
        return ActionResult.error(
            "Role must be 'user' or 'admin'.", retryable=False)

    generated = ""
    password = params.password or ""
    if not password:
        generated = secrets.token_urlsafe(_GENERATED_PASSWORD_BYTES)
        password = generated
    if len(password) < 10:
        return ActionResult.error(
            "Password must be at least 10 characters (or leave it empty to "
            "auto-generate one).", retryable=False)

    try:
        resp = await queries.create_sharelock_user(
            email=email, password=password, role=role,
            agency_id=state.agency_id, name=params.name or "",
        )
    except queries.CasesAPIError as e:
        if getattr(e, "status", None) == 409 or "already exists" in str(e):
            return ActionResult.success(
                data=RegisterUserResult(
                    email=email, reason="account_exists").model_dump(),
                summary=f"A Sharelock account for {email} already exists.",
            )
        log.warning(f"register_user: create failed for {email}: {e}")
        return ActionResult.error(
            "Account creation did not complete — the Cases service refused "
            "it. Check the email and try again.", retryable=True)
    except Exception as e:
        log.warning(f"register_user: create failed for {email}: {e}")
        return ActionResult.error(
            "Account creation did not complete — the Cases service did not "
            "confirm it. Try again.", retryable=True)

    result = RegisterUserResult(
        created=True, email=str(resp.get("email") or email),
        role=str(resp.get("role") or role),
        agency_id=str(resp.get("agency_id") or state.agency_id),
        generated_password=generated,
    )
    summary = (f"Sharelock account created: {result.email} "
               f"(role: {result.role}, agency: {result.agency_id}).")
    if generated:
        summary += (f" Generated password (shown ONCE — hand it to the user "
                    f"securely): {generated}")
    else:
        summary += " They can sign in with the password you set."
    return ActionResult.success(data=result.model_dump(), summary=summary)
