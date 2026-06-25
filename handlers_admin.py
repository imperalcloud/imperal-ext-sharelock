"""
Sharelock v2 — Admin settings handler (per-agency storage + user-store DB).

The Cases API ``PUT /agency/{id}/storage`` REPLACES the whole encrypted blob,
so this handler MERGES: fetch current settings, overlay non-empty submitted
fields (an empty password/field means KEEP the existing value), then PUT the
merged body. The agency key is ``_user_agency(ctx)`` — the SAME key
``files.get_agency_backend`` resolves storage with, so a save here applies to
the exact backend the panels/handlers read through.

Secrets are NEVER echoed in facts/summaries — masked to set/not-set
(I-SECRETS-HANDLER-SCOPE-MEMORY discipline; SaveSettingsResponse models no
secret field at all).
"""
import logging

from pydantic import BaseModel, Field

from app import chat, _user_id, _user_agency
import auth_gate
from auth_gate import require_unlock
from imperal_sdk.chat import ActionResult
import files
import queries
from queries import CasesAPIError
from models import SaveSettingsResponse

log = logging.getLogger("sharelock-v2.handlers_admin")


# ── Parameter Model ───────────────────────────────────────────────────────────


class SaveAgencySettingsParams(BaseModel):
    """Settings form fields. Empty string = keep the current stored value
    (the panel form only submits fields the admin actually edited)."""
    storage_url: str = Field("", description="Nextcloud URL (empty = keep current)")
    storage_username: str = Field("", description="Storage username (empty = keep current)")
    storage_password: str = Field("", description="Storage password (empty = keep current)")
    storage_base_path: str = Field("", description="Storage base path, e.g. /Sharelock/ (empty = keep current)")
    db_dsn: str = Field("", description="User-store DB DSN (optional; overrides discrete fields)")
    db_host: str = Field("", description="User-store DB host (empty = keep current)")
    db_port: str = Field("", description="User-store DB port (empty = keep current)")
    db_name: str = Field("", description="User-store DB name (empty = keep current)")
    db_username: str = Field("", description="User-store DB username (empty = keep current)")
    db_password: str = Field("", description="User-store DB password (empty = keep current)")


# ── Merge helpers (pure — unit-tested) ────────────────────────────────────────


def _merge_database(current_db, params: SaveAgencySettingsParams):
    """Merged ``database`` section for the PUT body, or None to omit.

    DSN submitted → dsn-mode (replaces any discrete config). Discrete
    fields submitted → overlay onto existing discrete fields (a stored
    dsn-mode config is replaced, not mixed). Nothing submitted → keep the
    existing section verbatim (PUT replaces the whole blob).
    """
    dsn = params.db_dsn.strip()
    if dsn:
        return {"dsn": dsn}
    cur = current_db if isinstance(current_db, dict) else {}
    submitted = {
        "host": params.db_host.strip(),
        "port": params.db_port.strip(),
        "name": params.db_name.strip(),
        "username": params.db_username,
        "password": params.db_password,
    }
    if not any(submitted.values()):
        return cur or None
    if "dsn" in cur:
        cur = {}  # discrete fields submitted — leave dsn-mode entirely
    return {k: v or cur.get(k, "") for k, v in submitted.items()}


def _merge_settings(current: dict, params: SaveAgencySettingsParams) -> dict:
    """Full PUT body: current settings overlaid with non-empty submitted
    fields. ``current`` is the GET /agency/{id}/storage response."""
    configured = bool(current.get("configured"))
    nc_cur = ((current.get("storage") or {}).get("nextcloud") or {}) if configured else {}
    nc = {
        "url": params.storage_url.strip() or nc_cur.get("url", ""),
        "username": params.storage_username.strip() or nc_cur.get("username", ""),
        "password": params.storage_password or nc_cur.get("password", ""),
        "base_path": params.storage_base_path.strip() or nc_cur.get("base_path", ""),
    }
    body = {"storage": {"backend": "nextcloud", "nextcloud": nc}}
    db = _merge_database(current.get("database") if configured else None, params)
    if db is not None:
        body["database"] = db
    return body


# ── Chat Function ─────────────────────────────────────────────────────────────


@chat.function("save_agency_settings", action_type="write",
               effects=["update:agency_settings"],
               data_model=SaveSettingsResponse,
               description=(
                   "Save per-agency storage (Nextcloud) and optional "
                   "user-store database settings. Requires the Sharelock "
                   "admin role; empty fields keep the current values."
               ))
@require_unlock
async def fn_save_agency_settings(ctx, params: SaveAgencySettingsParams) -> ActionResult:
    """Admin-gated merge-then-PUT of the agency settings blob."""
    # Via the module (not a bare-name import) so the gate and the role
    # check share one monkeypatch point — mirrors the existing tests.
    # force_fresh: authz reads the role LIVE (never the 60s unlock cache) so a
    # demoted admin cannot save storage credentials within the stale window.
    state = await auth_gate._fetch_unlock(ctx, force_fresh=True)
    if state.role != "admin":
        return ActionResult.success(
            data={"saved": False, "agency_id": state.agency_id,
                  "reason": "admin_role_required"},
            summary=("Saving agency settings requires the Sharelock admin "
                     "role — ask your agency supervisor."),
        )

    agency = _user_agency(ctx)
    try:
        current = await queries.get_agency_storage(agency)
    except Exception as e:
        log.warning(f"save_agency_settings: current-settings read failed "
                    f"for {agency}: {e}")
        current = {"configured": False}
    if not isinstance(current, dict):
        current = {"configured": False}

    body = _merge_settings(current, params)
    nc = body["storage"]["nextcloud"]
    if not nc["url"] or not nc["base_path"]:
        return ActionResult.error(
            "Storage URL and base path are required (no stored values to "
            "keep). Fill both fields and save again.", retryable=False)
    db = body.get("database")
    if db and "dsn" not in db and (not db.get("host") or not db.get("name")):
        return ActionResult.error(
            "Database host and name are required when configuring discrete "
            "DB fields (or use a single DSN).", retryable=False)

    body["updated_by"] = _user_id(ctx) or "sharelock-v2"
    try:
        await queries.put_agency_storage(agency, body)
    except CasesAPIError as e:
        return ActionResult.error(f"Failed to save settings: {e.detail or e}",
                                  retryable=False)
    except Exception as e:
        return ActionResult.error(f"Failed to save settings: {e}")

    # Apply immediately — drop the in-process backend TTL cache so the next
    # storage read resolves with the new credentials.
    files.reset_backend_cache()

    return ActionResult.success(
        data={"saved": True, "agency_id": agency,
              "storage_url": nc["url"], "storage_username": nc["username"],
              "storage_base_path": nc["base_path"],
              "storage_password_set": bool(nc["password"]),
              "database_configured": bool(db)},
        summary=(f"Agency settings saved for '{agency}': storage {nc['url']} "
                 f"(user {nc['username'] or '—'}, base {nc['base_path']}), "
                 f"password {'set' if nc['password'] else 'not set'}; "
                 f"database {'configured' if db else 'not configured'}."),
    )
