"""
Sharelock v2 — Right panel: Analysis / Gap Review / Graph / Report tabs + Create Case form.

section = Nextcloud folder name (string). Cross-panel sync: when sidebar
clicks a folder, the right panel receives section=<folder> from the Panel
shell. In that case we treat section as the folder name and look up the
matching Cases API case.

Per I-SKELETON-LLM-ONLY (SDK v1.6.0) the analysis tab reads its per-render
case snapshot from ``ctx.cache`` (model ``CaseSummary``) rather than
``ctx.skeleton_data``. The skeleton workflow still writes the scalar
classifier envelope; this cache layer carries the progress / files /
entities payload panels need to render.
"""
import logging

from imperal_sdk import ui
from app import ext, _user_id, _user_agency, CASES_API_URL
from auth_gate import _fetch_unlock, locked_panel
import queries
import panels_analysis as pa
from cache_models import CaseSummary, thin_case_summary_data
# Module-level on purpose (NOT inside _fetch): bare ext module names resolve
# correctly only while the loader imports this extension; a runtime-lazy
# import re-executes case_resolver against another ext's namespace
# (same incident class as files.get_agency_backend, 2026-06-12).
from case_resolver import load_case_data_from_api
from panels import _cached_user_cases  # circuit-breaker for Cases API panel reads
from panels_gap_review import build_gap_review
from panels_graph import build_graph_panel
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
    if not unlock.unlocked:
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

    # ── Tab Content ───────────────────────────────────────────────────────
    try:
        if tab == "analysis":
            content = await _build_analysis_tab(ctx, case_id)
        elif tab == "gap_review":
            content = await _build_gap_review_tab(ctx, case_id)
        elif tab == "graph":
            content = await _build_graph_tab(ctx, case_id)
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


async def _get_api_case(ctx, folder_name: str) -> dict:
    """Find Cases API case matching this Nextcloud folder name.

    Uses cached fast-or-stale helper to keep panel responsive when Cases
    API is overloaded (fast-RPC deadline -> Temporal-fallback otherwise).
    """
    user_id = _user_id(ctx)
    try:
        cases = await _cached_user_cases(ctx, user_id)
        for c in cases:
            if c.get("name", "").strip() == folder_name.strip():
                return c
    except Exception as e:
        log.warning(f"_get_api_case folder={folder_name!r} unexpected: {e}")
    return {}


def _resolve_api_case_id(api_case: dict) -> int | None:
    """Return numeric case_id from the Cases API row, or None."""
    cid = api_case.get("id") if api_case else None
    try:
        return int(cid) if cid is not None else None
    except (TypeError, ValueError):
        return None


async def _load_case_summary(ctx, api_case_id: int | None) -> CaseSummary:
    """Fetch the active-case snapshot via ``ctx.cache`` (SDK v1.6.0).

    Replaces the legacy ``ctx.skeleton_data["case_status"]`` read path.
    ttl=60s keeps the panel responsive while still reducing Cases API
    churn on tab switches within a single render pass.
    """
    user_id = _user_id(ctx)

    async def _fetch() -> CaseSummary:
        try:
            # Reuse the deterministic loader so the cache ends up in the
            # exact shape the chat path and skeleton also consume.
            data = await load_case_data_from_api(
                user_id, int(api_case_id or 0),
                agency_id=_user_agency(ctx))
        except Exception as exc:
            log.warning(f"case_summary fetch failed for case {api_case_id}: {exc}")
            data = {}
        # I-CACHE-VALUE-SIZE-CAP-64KB: cap files[] before the cache write
        # (live incident: «Alex Case 1», 2655 files -> 142KB envelope).
        data = thin_case_summary_data(data)
        return CaseSummary(**{k: v for k, v in data.items()
                               if k in CaseSummary.model_fields})

    key_case = int(api_case_id) if api_case_id else 0
    return await ctx.cache.get_or_fetch(
        key=f"case_summary:{user_id}:{key_case}",
        model=CaseSummary,
        fetcher=_fetch,
        ttl_seconds=60,
    )


async def _build_analysis_tab(ctx, folder_name: str):
    """Analysis tab: shows status from Cases API if case exists.

    B3: when a run is active, show progress + Cancel + confidence badge.
    """
    api_case = await _get_api_case(ctx, folder_name)
    api_case_id = _resolve_api_case_id(api_case)
    analysis_status = api_case.get("analysis_status") if api_case else None

    # SDK v1.6.0: case snapshot via ctx.cache (was ctx.skeleton_data).
    summary = await _load_case_summary(ctx, api_case_id)
    progress = summary.analysis_progress or {}
    outdated = summary.outdated
    version = summary.analysis_version

    if not api_case:
        return ui.Stack(children=[
            ui.Alert(title="Not Registered",
                     message=(f"Folder '{folder_name}' exists in Nextcloud but is not yet "
                              f"registered as a case. Register it first to run analysis."),
                     type="info"),
            ui.Button(label=f"Register '{folder_name}' as Case", variant="primary",
                      on_click=ui.Call("create_case", name=folder_name,
                                       description=f"From Nextcloud: {folder_name}")),
        ])

    if analysis_status == "running" and progress:
        run = {}
        try:
            run = await queries.get_latest_active_run(
                api_case_id, agency_id=_user_agency(ctx))
        except Exception as exc:
            log.warning(f"analysis tab: failed to load run for case {api_case_id}: {exc}")
        return pa.build_progress_with_controls(progress, api_case_id, run)

    if analysis_status == "completed" and outdated:
        new_files = sum(1 for f in summary.files if f.get("status") == "new")
        return pa.build_outdated(api_case_id, new_files, version)

    if analysis_status == "completed":
        return ui.Stack(children=[
            ui.Alert(title="Analysis Complete",
                     message="View the report in the Report tab.", type="info"),
            ui.Stats(columns=3, children=[
                ui.Stat(label="Version", value=f"v{version}", icon="FileText", color="blue"),
                ui.Stat(label="Case ID", value=str(api_case_id), icon="Hash", color="gray"),
                ui.Stat(label="Entities",
                        value=str(len(summary.key_entities)),
                        icon="Users", color="green"),
            ]),
            ui.Button(label="Re-analyze", variant="ghost",
                      on_click=ui.Call("run_analysis", case_id=api_case_id)),
        ])

    if analysis_status == "error":
        return ui.Stack(children=[
            ui.Alert(title="Analysis Failed", message="Please try again.", type="error"),
            ui.Button(label="Retry Analysis", variant="primary",
                      on_click=ui.Call("run_analysis", case_id=api_case_id)),
        ])

    # Not started
    return ui.Stack(children=[
        ui.Text("Ready for Analysis"),
        ui.Text(f"Case '{folder_name}' is registered (ID: {api_case_id}). "
                f"Upload documents to Nextcloud and run analysis."),
        ui.Button(label="Run Analysis", variant="primary",
                  on_click=ui.Call("run_analysis", case_id=api_case_id)),
    ])


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


async def _build_gap_review_tab(ctx, folder_name: str):
    """Gap Review tab: delegates to panels_gap_review."""
    api_case = await _get_api_case(ctx, folder_name)
    api_case_id = _resolve_api_case_id(api_case)
    if api_case_id is None:
        return ui.Alert(title="Not Registered",
                        message="Register this folder as a case first.",
                        type="info")
    return await build_gap_review(api_case_id, agency_id=_user_agency(ctx))


async def _build_graph_tab(ctx, folder_name: str):
    """Graph tab: delegates to panels_graph (DataTable view, Session A)."""
    api_case = await _get_api_case(ctx, folder_name)
    api_case_id = _resolve_api_case_id(api_case)
    if api_case_id is None:
        return ui.Alert(title="Not Registered",
                        message="Register this folder as a case first.",
                        type="info")
    return await build_graph_panel(api_case_id, agency_id=_user_agency(ctx))


async def _build_report_tab(ctx, folder_name: str):
    """Report tab.

    The Download PDF button opens an HMAC-signed URL in a new tab via
    ``ui.Open(url=...)`` — the Cases API accepts the token as an
    alternative to the ``x-api-key`` header so ``window.open`` works
    without JS-level header injection. The signed URL is minted on every
    panel render (TTL 600s) so stale panels simply re-render to refresh.
    """
    api_case = await _get_api_case(ctx, folder_name)
    if not api_case:
        return ui.Text("Register this folder as a case first.")

    case_id = _resolve_api_case_id(api_case)
    analysis_status = api_case.get("analysis_status")

    if analysis_status != "completed":
        return ui.Stack(children=[
            ui.Text("No Report Available"),
            ui.Text("Run analysis first to generate a forensic intelligence report."),
        ])

    agency = _user_agency(ctx)
    try:
        analysis = await queries.get_analysis(case_id, agency_id=agency)
        report_text = analysis.get("analysis_result", "")
    except Exception:
        report_text = ""

    if not report_text:
        return ui.Stack(children=[
            ui.Alert(title="Report Unavailable",
                     message="Analysis completed but report data is missing.",
                     type="warning"),
            ui.Button(label="Re-analyze", variant="primary",
                      on_click=ui.Call("run_analysis", case_id=case_id)),
        ])

    # Resolve run_id for the report URL (prefer latest completed run), then
    # mint a short-lived signed URL so the browser can download without
    # needing the x-api-key header.
    report_url = ""
    incomplete = False
    try:
        runs = await queries.list_runs(case_id, agency_id=agency)
        completed = [r for r in runs if r.get("status") == "completed"]
        chosen = completed[0] if completed else (runs[0] if runs else None)
        if chosen and chosen.get("run_id") is not None:
            run_id = int(chosen["run_id"])
            incomplete = chosen.get("status") != "completed"
            try:
                signed = await queries.sign_report_url(
                    case_id, run_id, fmt="pdf", ttl=600,
                    agency_id=agency,
                )
                report_url = signed.get("url", "")
                # If we're forced to use an incomplete run, add the flag
                # so the server renders it (signed URL was minted for pdf,
                # incomplete flag is a separate query param that does not
                # change the HMAC payload).
                if incomplete and report_url:
                    sep = "&" if "?" in report_url else "?"
                    report_url = f"{report_url}{sep}allow_incomplete=true"
            except Exception as exc:
                log.warning(
                    f"report tab: sign_report_url failed for case {case_id} "
                    f"run {run_id}: {exc}"
                )
    except Exception as exc:
        log.warning(f"report tab: failed to resolve run for case {case_id}: {exc}")

    case_name = api_case.get("name", folder_name)

    if report_url:
        header_children = [
            ui.Button(label="Download PDF", variant="primary", icon="Download",
                      on_click=ui.Open(url=report_url)),
        ]
    else:
        header_children = [
            ui.Alert(title="Report URL Unavailable",
                     message=("Could not generate a signed download URL. "
                              "Please refresh the panel or contact support."),
                     type="warning"),
        ]
    header = ui.Section(title=f"Report: {case_name}", children=header_children)

    preview_text = report_text[:3000]
    if len(report_text) > 3000:
        preview_text += "\n\n[... Download PDF for full report ...]"
    report_section = ui.Section(title="Executive Summary",
                                children=[ui.Text(preview_text)])

    return ui.Stack(children=[header, report_section])
