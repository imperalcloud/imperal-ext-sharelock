"""
Sharelock v2 — Admin Settings tab builder (right panel, admin-only).

Shows the current per-agency settings MASKED (secrets render as set/not-set;
the DSN — which may embed a password — is never prefilled or displayed) plus
one form (storage + optional user-store DB groups) submitting to
``save_agency_settings``. Password inputs are ``ui.Password`` — emits
``UINode(type='Input', props.type='password')``, which the deployed DInput
renders as ``<input type="password" autocomplete="new-password">``.

Untouched prefilled inputs are NOT included in the submitted values (DForm
only collects fields the admin edits), and the handler's merge treats empty/
missing fields as keep-current — so leaving the password blank keeps the
stored secret.
"""
import logging

from imperal_sdk import ui

from app import _user_agency
import queries

log = logging.getLogger("sharelock-v2.panels_settings")


def _mask(value) -> str:
    return "set" if value else "not set"


async def build_settings_tab(ctx):
    """Settings tab (caller enforces the admin role)."""
    agency = _user_agency(ctx)
    try:
        current = await queries.get_agency_storage(agency)
    except Exception as exc:
        log.warning(f"settings tab: get_agency_storage failed for "
                    f"{agency}: {exc}")
        current = {"configured": False}
    if not isinstance(current, dict):
        current = {"configured": False}

    configured = bool(current.get("configured"))
    nc = ((current.get("storage") or {}).get("nextcloud") or {}) if configured else {}
    db = current.get("database") if configured else None
    db = db if isinstance(db, dict) else {}

    state_items = [
        {"key": "Agency", "value": agency},
        {"key": "Storage URL", "value": nc.get("url") or "—"},
        {"key": "Storage user", "value": nc.get("username") or "—"},
        {"key": "Base path", "value": nc.get("base_path") or "—"},
        {"key": "Storage password", "value": _mask(nc.get("password"))},
    ]
    if db.get("dsn"):
        state_items.append({"key": "Database", "value": "configured (DSN set)"})
    elif db:
        state_items += [
            {"key": "Database host", "value": db.get("host") or "—"},
            {"key": "Database name", "value": db.get("name") or "—"},
            {"key": "Database password", "value": _mask(db.get("password"))},
        ]
    else:
        state_items.append({"key": "Database", "value": "not configured"})

    current_section = ui.Section(
        title=("Current settings" if configured
               else "Current settings (not configured — env fallback active)"),
        children=[ui.KeyValue(items=state_items)],
    )

    form = ui.Form(
        action="save_agency_settings",
        submit_label="Save settings",
        children=[
            ui.Text("Storage (Nextcloud)"),
            ui.Input(param_name="storage_url", value=nc.get("url") or "",
                     placeholder="https://cloud.example.org"),
            ui.Input(param_name="storage_username",
                     value=nc.get("username") or "",
                     placeholder="storage username"),
            ui.Password(param_name="storage_password",
                        placeholder="leave empty to keep current"),
            ui.Input(param_name="storage_base_path",
                     value=nc.get("base_path") or "",
                     placeholder="/Sharelock/"),
            ui.Divider(),
            ui.Text("User-store database (optional)"),
            # DSN may embed credentials — never prefilled.
            ui.Input(param_name="db_dsn",
                     placeholder="DSN (optional — overrides the fields below)"),
            ui.Input(param_name="db_host", value=db.get("host") or "",
                     placeholder="db host"),
            ui.Input(param_name="db_port", value=db.get("port") or "",
                     placeholder="db port"),
            ui.Input(param_name="db_name", value=db.get("name") or "",
                     placeholder="db name"),
            ui.Input(param_name="db_username", value=db.get("username") or "",
                     placeholder="db username"),
            ui.Password(param_name="db_password",
                        placeholder="leave empty to keep current"),
        ],
    )

    return ui.Stack(children=[
        current_section,
        ui.Section(title="Update settings", children=[
            form,
            ui.Text("Empty fields keep the current values; passwords are "
                    "stored encrypted and never displayed."),
        ]),
    ])
