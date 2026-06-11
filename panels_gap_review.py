"""
Sharelock v2 — Gap Review panel builder.

Renders the gap-review step of the analysis pipeline when the latest run is
paused in state `gap_review`. The operator can choose to:

* Continue analysis with current evidence (confidence stays at `current`).
* Upload additional evidence and start a new run (confidence moves toward
  `potential`).

Consumed by panels_case via the `gap_review` sub-tab.
"""
from __future__ import annotations

import logging

from imperal_sdk import ui
import queries

log = logging.getLogger("sharelock-v2.panels_gap_review")

_SEVERITY_COLORS = {
    "BLOCKING": "red",
    "QUALITY": "yellow",
    "INFORMATIONAL": "gray",
}


def _pct(value) -> str:
    """Format confidence decimal as 0-decimal percentage, or '—' if None."""
    if value is None:
        return "—"
    try:
        return f"{float(value):.0%}"
    except (TypeError, ValueError):
        return "—"


def _confidence_stats(run: dict) -> ui.UINode:
    """Stats row: current vs potential confidence."""
    cur = run.get("confidence_current")
    pot = run.get("confidence_potential")
    delta_val = "—"
    try:
        if cur is not None and pot is not None:
            delta_val = f"+{(float(pot) - float(cur)):.0%}"
    except (TypeError, ValueError):
        delta_val = "—"

    return ui.Stats(columns=3, children=[
        ui.Stat(label="Current Confidence", value=_pct(cur),
                icon="ShieldCheck", color="blue"),
        ui.Stat(label="Potential Confidence", value=_pct(pot),
                icon="TrendingUp", color="green"),
        ui.Stat(label="Delta", value=delta_val,
                icon="Plus", color="gray"),
    ])


def _gap_list_section(title: str, sev: str, gaps: list) -> ui.UINode:
    """Render a Section listing gaps of one severity."""
    color = _SEVERITY_COLORS.get(sev, "gray")
    items = []
    for g in gaps[:25]:
        desc = (g.get("description") or "").strip()
        first_line = desc.split("\n")[0][:300]
        rec = g.get("recommended_evidence") or ""
        gap_type = g.get("gap_type") or sev
        subtitle_parts = [gap_type]
        if rec:
            subtitle_parts.append(f"Needs: {rec[:100]}")
        items.append(ui.ListItem(
            id=str(g.get("gap_id", "")),
            title=first_line or "(no description)",
            subtitle=" — ".join(subtitle_parts),
            badge=ui.Badge(sev, color=color),
        ))

    if not items:
        return ui.Section(title=title, children=[
            ui.Text("None flagged.", variant="caption"),
        ])

    children: list = [ui.List(items=items)]
    if len(gaps) > 25:
        children.append(ui.Text(f"...and {len(gaps) - 25} more", variant="caption"))
    return ui.Section(title=title, children=children)


def _decision_bar(case_id, has_blocking: bool) -> ui.UINode:
    """Continue / Upload Evidence decision buttons."""
    continue_variant = "ghost" if has_blocking else "primary"
    continue_label = ("Continue Anyway" if has_blocking else "Continue Analysis")
    return ui.Stack(direction="h", gap=2, children=[
        ui.Button(
            label=continue_label,
            variant=continue_variant,
            icon="Play",
            on_click=ui.Call("continue_analysis", case_id=case_id),
        ),
        ui.Button(
            label="Upload Evidence & Re-run",
            variant="primary" if has_blocking else "secondary",
            icon="Upload",
            on_click=ui.Call("resume_with_new_evidence", case_id=case_id),
        ),
    ])


async def build_gap_review(case_id: int,
                           agency_id: str | None = None) -> ui.UINode:
    """Build the Gap Review view for a case. Fetches run + gaps via queries."""
    try:
        run = await queries.get_latest_active_run(case_id, agency_id=agency_id)
    except Exception as exc:
        log.error(f"gap_review: failed to load run case_id={case_id}: {exc}")
        return ui.Alert(title="Gap review unavailable",
                        message=f"Could not load run status: {exc}",
                        type="error")

    if not run:
        return ui.Stack(children=[
            ui.Alert(title="No analysis runs yet",
                     message="Run analysis first to see gap review.",
                     type="info"),
            ui.Button(label="Run Analysis", variant="primary",
                      on_click=ui.Call("run_analysis", case_id=case_id)),
        ])

    run_id = run.get("run_id")
    status = run.get("status", "unknown")

    try:
        gaps = await queries.list_gaps(case_id, run_id, agency_id=agency_id)
    except Exception as exc:
        log.error(f"gap_review: failed to load gaps case_id={case_id} run_id={run_id}: {exc}")
        gaps = []
    # Carry-forward: when V5 incremental skips Phase 4.5 gap_detection on
    # a no-change re-run, the active run has 0 gaps but prior runs still
    # do. Re-query with run_id=None so the Cases API server-side
    # carry-forward (routers/gaps.list_gaps -> MAX(run_id) WHERE gaps
    # exist) fires. Federal-grade: panel must show prior gaps so the
    # investigator does not think the case is gap-free.
    carried_forward_from = None
    if not gaps:
        try:
            gaps = await queries.list_gaps(case_id, None, agency_id=agency_id)
            if gaps:
                first_gap_run = gaps[0].get("run_id")
                if first_gap_run and first_gap_run != run_id:
                    carried_forward_from = first_gap_run
        except Exception as exc:
            log.warning(f"gap_review: carry-forward fetch failed case={case_id}: {exc}")

    # Carry-forward confidence stats too — V5 incremental skips Phase 5
    # which computes confidence_current/potential, leaving the run row
    # with NULL values that render as dashes in the UI. When we already
    # carried gaps forward from an older run, pull confidence from THAT
    # run too so the UI shows real numbers instead of placeholders.
    if carried_forward_from and (
        run.get("confidence_current") is None
        or run.get("confidence_potential") is None
    ):
        try:
            old_run = await queries.get_run(case_id, carried_forward_from,
                                            agency_id=agency_id)
            for fld in ("confidence_current", "confidence_potential"):
                if run.get(fld) is None and old_run.get(fld) is not None:
                    run[fld] = old_run[fld]
        except Exception as exc:
            log.warning(
                f"gap_review: carried-run fetch failed "
                f"case={case_id} run={carried_forward_from}: {exc}"
            )

    by_sev: dict[str, list] = {"BLOCKING": [], "QUALITY": [], "INFORMATIONAL": []}
    for g in gaps:
        by_sev.setdefault(g.get("severity", "INFORMATIONAL"), []).append(g)

    blocking = by_sev.get("BLOCKING", [])
    quality = by_sev.get("QUALITY", [])
    info = by_sev.get("INFORMATIONAL", [])

    # Header + status alert
    children: list = []
    if status == "gap_review":
        children.append(ui.Alert(
            title="Analysis paused — gap review",
            message=(f"Run #{run_id} is paused so you can review identified gaps "
                     f"before the report is generated."),
            type="warning",
        ))
    else:
        if carried_forward_from:
            children.append(ui.Alert(
                title=f"Run #{run_id} produced no gaps — showing run #{carried_forward_from}",
                message=(
                    f"The latest analysis run was incremental (no file changes), "
                    f"so Phase 4.5 gap detection was skipped. Gaps below are "
                    f"carried forward from the most recent run that flagged any."
                ),
                type="warning",
            ))
        else:
            children.append(ui.Alert(
                title=f"Run #{run_id} status: {status}",
                message="Gaps below reflect the most recent run for this case.",
                type="info",
            ))

    children.append(_confidence_stats(run))

    if blocking:
        children.append(_gap_list_section(
            f"Blocking Gaps ({len(blocking)})", "BLOCKING", blocking))
    if quality:
        children.append(_gap_list_section(
            f"Quality Gaps ({len(quality)})", "QUALITY", quality))
    if info:
        children.append(_gap_list_section(
            f"Informational ({len(info)})", "INFORMATIONAL", info))

    if not gaps:
        children.append(ui.Alert(
            title="No gaps flagged",
            message="This run passed gap review with no outstanding issues.",
            type="info",
        ))

    if status == "gap_review":
        children.append(ui.Section(
            title="Decision",
            children=[
                ui.Text("Choose how to proceed with this run.", variant="caption"),
                _decision_bar(case_id, bool(blocking)),
            ],
        ))

    return ui.Stack(children=children, gap=3)
