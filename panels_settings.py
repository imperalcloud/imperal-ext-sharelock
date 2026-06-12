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

Copy doctrine (Valentin, 2026-06-12): every section explains ITSELF — what
it controls, what the default means, and when an agency should touch it.
«Database: not configured» confused the first admin into thinking the
product had no database — the platform DB is infrastructure, not a setting.
"""
import logging

from imperal_sdk import ui

from app import _user_agency
import queries

log = logging.getLogger("sharelock-v2.panels_settings")

_MANAGED_DB_LABEL = "Sharelock managed platform database (default)"


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
        {"key": "Evidence storage", "value": nc.get("url") or "—"},
        {"key": "Storage account", "value": nc.get("username") or "—"},
        {"key": "Storage folder", "value": nc.get("base_path") or "—"},
        {"key": "Storage password", "value": _mask(nc.get("password"))},
    ]
    if db.get("dsn"):
        state_items.append(
            {"key": "User database", "value": "agency-hosted (external DSN set)"})
    elif db:
        state_items += [
            {"key": "User database", "value": "agency-hosted (external)"},
            {"key": "Database host", "value": db.get("host") or "—"},
            {"key": "Database name", "value": db.get("name") or "—"},
            {"key": "Database password", "value": _mask(db.get("password"))},
        ]
    else:
        state_items.append({"key": "User database", "value": _MANAGED_DB_LABEL})

    current_section = ui.Section(
        title=("Current settings" if configured
               else "Current settings (platform defaults active)"),
        children=[ui.KeyValue(items=state_items)],
    )

    form = ui.Form(
        action="save_agency_settings",
        submit_label="Save settings",
        children=[
            ui.Text("Evidence storage"),
            ui.Text("Where your agency's case files (evidence) physically "
                    "live. By default this is the secure storage space "
                    "provisioned for your agency by the platform — analysis "
                    "and the file panels read from here. Change these "
                    "credentials ONLY to point Sharelock at storage your "
                    "agency hosts itself (your own cloud); coordinate the "
                    "migration with support first, or existing case files "
                    "will not be visible at the new location."),
            ui.Input(param_name="storage_url", value=nc.get("url") or "",
                     placeholder="Storage server URL (e.g. https://cloud.agency.gov)"),
            ui.Input(param_name="storage_username",
                     value=nc.get("username") or "",
                     placeholder="Storage account name"),
            ui.Password(param_name="storage_password",
                        placeholder="Storage password — leave empty to keep the current one"),
            ui.Input(param_name="storage_base_path",
                     value=nc.get("base_path") or "",
                     placeholder="Folder for case files (e.g. /Sharelock/)"),
            ui.Divider(),
            ui.Text("Agency user database (optional, advanced)"),
            ui.Text("By default your agency's users and case records live in "
                    "the Sharelock managed platform database — a replicated, "
                    "highly-available cluster operated for you. Nothing to "
                    "configure: «" + _MANAGED_DB_LABEL + "» above means "
                    "everything is normal. Fill this section ONLY if your "
                    "agency is required to host its own user database "
                    "(data-sovereignty mandates). It takes effect once "
                    "external user-store support is enabled for your agency "
                    "— until then the values are stored encrypted and "
                    "validated, nothing switches over."),
            # DSN may embed credentials — never prefilled.
            ui.Input(param_name="db_dsn",
                     placeholder="Connection DSN — optional, overrides the fields below"),
            ui.Input(param_name="db_host", value=db.get("host") or "",
                     placeholder="Database host (e.g. db.agency.gov)"),
            ui.Input(param_name="db_port", value=db.get("port") or "",
                     placeholder="Database port (e.g. 3306)"),
            ui.Input(param_name="db_name", value=db.get("name") or "",
                     placeholder="Database name"),
            ui.Input(param_name="db_username", value=db.get("username") or "",
                     placeholder="Database username"),
            ui.Password(param_name="db_password",
                        placeholder="Database password — leave empty to keep the current one"),
        ],
    )

    return ui.Stack(children=[
        current_section,
        ui.Section(title="Update settings", children=[
            form,
            ui.Text("Empty fields keep the current values. Passwords are "
                    "encrypted at rest and never displayed back — «set» "
                    "above confirms one is stored."),
        ]),
    ])
