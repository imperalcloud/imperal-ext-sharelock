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

from app import _user_agency, _user_id
import auth_gate
import queries

log = logging.getLogger("sharelock-v2.panels_settings")

_MANAGED_DB_LABEL = "Sharelock managed platform database (default)"


def _mask(value) -> str:
    return "set" if value else "not set"


async def _users_admin_block(ctx):
    """Agency user list with per-user role toggle + revoke (admin console).

    Caller (build_settings_tab) already gated on the admin role. Fail-soft:
    a Cases-API hiccup renders a note, never breaks the settings tab."""
    agency = _user_agency(ctx)
    me = _user_id(ctx)
    try:
        users = await queries.list_sharelock_users(agency)
    except Exception as exc:
        log.warning(f"users admin block: list failed for {agency}: {exc}")
        return ui.Section(title="Users", children=[
            ui.Text("Could not load the user list right now — try reopening "
                    "Settings.")])
    if not isinstance(users, list):
        users = []

    rows = []
    for u in users:
        uid = u.get("id")
        role = u.get("role") or "user"
        active = bool(u.get("is_active"))
        is_me = str(u.get("imperal_id") or "") == me
        state = "active" if active else "disabled"
        rows.append(ui.Stack(direction="horizontal", wrap=True, gap=2, children=[
            ui.Stack(children=[
                ui.Text(f"{u.get('email') or ('user #' + str(uid))}"
                        + (" (you)" if is_me else "")),
                ui.Text(f"{role} · {state} · {u.get('case_count', 0)} case(s)"),
            ]),
            # Role toggle: promote a user, or demote an admin.
            ui.Button(
                label=("Make user" if role == "admin" else "Make admin"),
                variant="outline", size="sm", icon="ShieldCheck",
                on_click=ui.Call("sharelock_update_user", user_id=uid,
                                 role=("user" if role == "admin" else "admin"),
                                 is_active="")),
            # Active toggle.
            ui.Button(
                label=("Disable" if active else "Enable"),
                variant="outline", size="sm", icon="Power",
                on_click=ui.Call("sharelock_update_user", user_id=uid, role="",
                                 is_active=("disabled" if active else "active"))),
        ] + ([] if is_me else [
            # Never offer revoke on your own row (and the server refuses the
            # last admin anyway).
            ui.Button(label="Revoke access", variant="destructive", size="sm",
                      icon="Trash2",
                      on_click=ui.Call("sharelock_delete_user", user_id=uid)),
        ])))
        rows.append(ui.Divider())

    return ui.Section(title=f"Users ({len(users)})", children=(
        [ui.Text("Everyone in your agency's Sharelock. Change a role, "
                 "disable an account (signs them out immediately), or revoke "
                 "access. The last active admin cannot be removed.")]
        + (rows if rows else [ui.Text("No users yet.")])))


async def build_team_invite_tab(ctx):
    """Mint a single-use invite code and show it IN the panel (copyable),
    instead of a transient chat notification. The raw code exists only in the
    mint response, so it is rendered right here by the call that receives it.
    Admin-gated (caller enforces, and the mint re-checks server-side)."""
    state = await auth_gate._fetch_unlock(ctx, force_fresh=True)
    back = ui.Button(label="← Back to settings", variant="ghost", size="sm",
                     on_click=ui.Call("__panel__dashboard", tab="settings",
                                      section="", view="", case_id=""))
    if state.role != "admin":
        return ui.Stack(children=[back, ui.Alert(
            title="Admin only",
            message="Minting invites requires the Sharelock admin role.",
            type="info")])
    try:
        resp = await queries.create_invite(
            agency_id=state.agency_id, created_by_imperal_id=_user_id(ctx),
            ttl_days=7)
    except Exception as exc:
        log.warning(f"team invite mint failed: {exc}")
        return ui.Stack(children=[back, ui.Alert(
            title="Could not create an invite",
            message="The Cases service did not confirm it. Go back and try again.",
            type="error")])
    code = str(resp.get("invite_code") or "")
    if not code:
        return ui.Stack(children=[back, ui.Alert(
            title="Could not create an invite", message="No code was returned.",
            type="error")])
    return ui.Stack(children=[
        back,
        ui.Text("Invite code created"),
        ui.Text("Give this single-use code to your colleague. They open the "
                "Sharelock Register page, enter it, and choose their own "
                f"password. Valid until {resp.get('expires_at') or 'its expiry'} "
                "(UTC). Shown only once — copy it now."),
        ui.Markdown(f"`{code}`"),
        ui.Input(param_name="_invite_code", value=code),
        ui.Divider(),
        ui.Button(label="Generate another", variant="outline", icon="Ticket",
                  on_click=ui.Call("__panel__dashboard", tab="team_invite",
                                   section="", view="", case_id="")),
    ])


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

    # ── Team: admin-direct account creation ───────────────────────────────
    # Sharelock keeps its OWN user store (separate sign-in on top of the
    # Imperal session). This form creates an account in YOUR agency's store
    # directly — the alternative invite flow stays available on the sign-in
    # screen. The handler re-checks the admin role LIVE.
    team_form = ui.Form(
        action="sharelock_register_user",
        submit_label="Create account",
        children=[
            ui.Input(param_name="email",
                     placeholder="New user's email — their sign-in login"),
            ui.Input(param_name="name",
                     placeholder="Display name (optional)"),
            ui.Password(param_name="password",
                        placeholder="Initial password — leave empty to auto-generate"),
            ui.Select(param_name="role", value="user", options=[
                {"value": "user", "label": "User — works cases"},
                {"value": "admin", "label": "Admin — also manages the agency"},
            ]),
        ],
    )
    # Invite minting renders its CODE in the panel (not a chat notification):
    # the button navigates to the in-panel mint view which shows a copyable
    # code. The raw code exists only in the mint response, so it must be
    # displayed by the handler that receives it.
    invite_block = ui.Stack(children=[
        ui.Input(param_name="invite_email",
                 placeholder="Email hint (optional) — locks the code to this email"),
        ui.Button(label="Generate invite code", variant="outline", icon="Ticket",
                  on_click=ui.Call("__panel__dashboard", tab="team_invite",
                                   section="", view="", case_id="")),
    ])

    users_block = await _users_admin_block(ctx)

    team_section = ui.Section(title="Team", children=[
        ui.Text("Two ways to onboard a colleague into your agency's "
                "Sharelock (it has its own sign-in on top of Imperal):"),
        ui.Text("1. Create the account yourself — set (or auto-generate) "
                "their initial password and hand it over. If you leave the "
                "password empty, a strong one is generated and shown to you "
                "ONCE — have them treat it as temporary."),
        team_form,
        ui.Divider(),
        ui.Text("2. Or send an invite code — the colleague opens the "
                "Sharelock Register page, enters the code, and picks their "
                "own password. Codes are single-use and valid 7 days."),
        invite_block,
    ])

    return ui.Stack(children=[
        current_section,
        team_section,
        users_block,
        ui.Section(title="Update settings", children=[
            form,
            ui.Text("Empty fields keep the current values. Passwords are "
                    "encrypted at rest and never displayed back — «set» "
                    "above confirms one is stored."),
        ]),
    ])
