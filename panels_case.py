"""
Sharelock v2 — Right panel: tab dispatch + Create Case form + share/settings.

section = Nextcloud folder name (string). Cross-panel sync: when sidebar
clicks a folder, the right panel receives section=<folder> from the Panel
shell. In that case we treat section as the folder name and look up the
matching Cases API case.

Tab CONTENT builders (Analysis / Gap Review / Graph / Report) and the
folder→case resolution + ``ctx.cache`` summary loader live in
``panels_case_tabs.py`` (Rule-6 split) — this module owns the
``@ext.panel`` entrypoint, the tab bar and the share/settings wiring.
"""
import logging

from imperal_sdk import ui
from app import ext
from auth_gate import _agency_consistent, _fetch_unlock, locked_panel
from panels_case_tabs import (
    _build_analysis_tab,
    _build_gap_review_tab,
    _build_graph_tab,
    _build_report_tab,
    _get_api_case,
    _resolve_api_case_id,
)
from panels_share import build_share_tab
from panels_settings import build_settings_tab

log = logging.getLogger("sharelock-v2.panels_case")

_TABS = [
    ("analysis", "Analysis"),
    ("gap_review", "Gap Review"),
    ("graph", "Graph"),
    ("report", "Report"),
    ("share", "Share"),
]


@ext.panel("dashboard", slot="right", title="Case Details", icon="file-text",
           default_width=480, min_width=360, max_width=640)
async def panel_dashboard(ctx, tab: str = "analysis", view: str = "",
                          case_id: str = "", section: str = "", **kwargs):
    """Right panel: tabs + create form.

    Cross-panel sync (session 22): `section` from the sidebar ALWAYS wins
    over a stale `case_id` coming from callPanel() param merging. The old
    `case_id` is overwritten here to avoid showing the previous case after
    the user selected a new one.
    """
    unlock = await _fetch_unlock(ctx)
    if not (unlock.unlocked and _agency_consistent(ctx, unlock)):
        return locked_panel()
    is_admin = unlock.role == "admin"

    if section:
        case_id = section

    # ── Create Case Form ──────────────────────────────────────────────────
    if view == "create":
        return ui.Stack(children=[
            ui.Text("Create New Case"),
            ui.Text("A folder will be created in Nextcloud and a case registered."),
            ui.Form(
                action="create_case",
                submit_label="Create Case",
                children=[
                    ui.Input(param_name="name", placeholder="Case name (e.g. Operation Midnight)"),
                    ui.Input(param_name="description", placeholder="Description (optional)"),
                ],
            ),
        ])

    # ── Confirm Delete (mirrors view="create": rendered only on demand) ───
    # The Dialog opens immediately when rendered, so we DON'T render it on the
    # normal case view — a first button press sets view="confirm_delete" and
    # only then does this branch draw the confirm Dialog. Admin-gated (same
    # check as Settings) so a non-admin can never reach a destructive Call.
    if view == "confirm_delete":
        if not is_admin:
            return ui.Alert(title="Admin Only",
                            message="Deleting a case requires the Sharelock admin role.",
                            type="info")
        api_case = await _get_api_case(ctx, case_id)
        api_case_id = _resolve_api_case_id(api_case)
        back_to_case = ui.Call("__panel__dashboard", tab="analysis",
                               section="", view="", case_id=case_id)
        if api_case_id is None:
            return ui.Stack(children=[
                ui.Alert(title="Not Registered",
                         message="This folder is not a registered case — nothing to delete.",
                         type="info"),
                ui.Button(label="← Back", variant="ghost", size="sm",
                          on_click=back_to_case),
            ])
        case_name = api_case.get("name") or case_id
        return ui.Stack(children=[
            ui.Dialog(
                title="Delete case",
                content=ui.Text(
                    f"Delete case '{case_name}'? This moves it to deleted "
                    f"and cleans evidence files."),
                confirm_label="Delete",
                cancel_label="Cancel",
                on_confirm=ui.Call("delete_case", case_id=api_case_id),
            ),
            # Explicit Cancel: ui.Dialog has no on_cancel, so give the user a
            # way back to the case (clears view) without deleting.
            ui.Button(label="Cancel", variant="ghost", size="sm",
                      on_click=back_to_case),
        ])

    # ── Admin Settings (no case selection required) ───────────────────────
    if tab == "settings":
        if not is_admin:
            return ui.Alert(title="Admin Only",
                            message="Agency settings require the Sharelock admin role.",
                            type="info")
        try:
            content = await build_settings_tab(ctx)
        except Exception as exc:
            log.error(f"Panel tab 'settings' error: {exc}")
            content = ui.Alert(title="Error", message=str(exc)[:300], type="error")
        back = ui.Button(label="← Back", variant="ghost", size="sm",
                         on_click=ui.Call("__panel__dashboard", tab="analysis",
                                          section="", view="", case_id=case_id))
        return ui.Stack(children=[back, content])

    # ── No case selected ──────────────────────────────────────────────────
    if not case_id:
        no_case_children = [
            ui.Text("Select a case"),
            ui.Text("Choose a case from the sidebar, or create a new one."),
            ui.Button(label="+ New Case", variant="primary",
                      on_click=ui.Call("__panel__dashboard", view="create",
                                       tab="", section="", case_id="")),
        ]
        if is_admin:
            no_case_children.append(ui.Button(
                label="Agency Settings", variant="ghost", icon="Settings",
                on_click=ui.Call("__panel__dashboard", tab="settings",
                                 section="", view="", case_id="")))
        return ui.Stack(children=no_case_children)

    if not tab:
        tab = "analysis"

    # ── Tab Bar ───────────────────────────────────────────────────────────
    tabs = list(_TABS) + ([("settings", "Settings")] if is_admin else [])
    tab_buttons = []
    for tid, label in tabs:
        tab_buttons.append(ui.Button(
            label=label,
            variant="primary" if tid == tab else "ghost",
            size="sm",
            on_click=ui.Call("__panel__dashboard", tab=tid, section="",
                             view="", case_id=case_id),
        ))

    # Delete case (admin only): does NOT delete on click — routes to the
    # confirm_delete view (one extra confirm step before the destructive Call).
    if is_admin:
        tab_buttons.append(ui.Button(
            label="Delete case",
            variant="danger",
            size="sm",
            icon="Trash2",
            on_click=ui.Call("__panel__dashboard", view="confirm_delete",
                             tab="", section="", case_id=case_id),
        ))

    # ── Tab Content ───────────────────────────────────────────────────────
    try:
        if tab == "analysis":
            content = await _build_analysis_tab(ctx, case_id)
        elif tab == "gap_review":
            content = await _build_gap_review_tab(ctx, case_id)
        elif tab == "graph":
            # node_id from the cluster-overview click (cluster:<type>) drives
            # the graph drill-in; empty / non-cluster ids → overview.
            content = await _build_graph_tab(ctx, case_id,
                                             node_id=kwargs.get("node_id"))
        elif tab == "report":
            content = await _build_report_tab(ctx, case_id)
        elif tab == "share":
            content = await _build_share_tab(ctx, case_id)
        else:
            content = ui.Text("Unknown tab.")
    except Exception as exc:
        log.error(f"Panel tab '{tab}' error: {exc}")
        content = ui.Alert(title="Error", message=str(exc)[:300], type="error")

    children = [ui.Stack(children=tab_buttons, direction="h")]
    if tab == "analysis":
        # Evidence dropzone above the analysis content (upload_case_files
        # fires immediately on file-select with base64 payloads).
        try:
            upload = await _build_upload_section(ctx, case_id)
            if upload is not None:
                children.append(upload)
        except Exception as exc:
            log.warning(f"upload section failed for '{case_id}': {exc}")
    children.append(content)
    return ui.Stack(children=children)


async def _build_share_tab(ctx, folder_name: str):
    """Share tab: delegates to panels_share."""
    api_case = await _get_api_case(ctx, folder_name)
    api_case_id = _resolve_api_case_id(api_case)
    if api_case_id is None:
        return ui.Alert(title="Not Registered",
                        message="Register this folder as a case first.",
                        type="info")
    return await build_share_tab(ctx, api_case_id)


async def _build_upload_section(ctx, folder_name: str):
    """Evidence dropzone above the Analysis tab (None when unregistered —
    the analysis tab already offers the Register button)."""
    api_case = await _get_api_case(ctx, folder_name)
    api_case_id = _resolve_api_case_id(api_case)
    if api_case_id is None:
        return None
    return ui.Section(title="Upload evidence", children=[
        ui.FileUpload(accept="*", max_size_mb=10, multiple=True,
                      max_files=8, max_total_mb=25, param_name="files",
                      on_upload=ui.Call("upload_case_files",
                                        case_id=api_case_id)),
        ui.Text("New files are picked up by analysis on the next census run."),
    ])
