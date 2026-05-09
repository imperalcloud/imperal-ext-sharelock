"""
Sharelock v2 — Analysis progress builder.

Renders 8-phase pipeline progress from skeleton data. Also exports
`build_progress_with_controls` which augments the progress view with a Cancel
button and confidence badge when the run status is active (bug B3).
"""
from __future__ import annotations

from imperal_sdk import ui


PHASES = [
    ("1", "Ingest & Classify"),
    ("2", "Triage & Priority"),
    ("3", "Deep Analysis"),
    ("3.5", "Adversarial Validation"),
    ("4", "Synthesis"),
    ("4.5", "Cross-Case Intel"),
    ("4.7", "Predictive"),
    ("5", "Report Generation"),
]

# Phase string → numeric sort key.
def _phase_num(s: str) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def _pct(value) -> str:
    """Format confidence decimal as 0-decimal percent, '—' if None."""
    if value is None:
        return "—"
    try:
        return f"{float(value):.0%}"
    except (TypeError, ValueError):
        return "—"


def build_progress(progress: dict, case_id) -> ui.UINode:
    """Build 8-phase progress view from analysis_progress skeleton data.

    Kept as a minimal builder (no controls). For the controlled variant use
    `build_progress_with_controls`.
    """
    current_phase = str(progress.get("phase", 0))
    phase_name = progress.get("phase_name", "")
    files_done = progress.get("files_done", 0)
    files_total = progress.get("files_total", 0)
    percent = progress.get("percent", 0)

    phase_items = []
    for phase_id, phase_label in PHASES:
        if _phase_num(phase_id) < _phase_num(current_phase):
            color = "green"
            status = "Done"
        elif phase_id == current_phase:
            color = "yellow"
            status = f"{files_done}/{files_total}" if files_total else f"{percent}%"
        else:
            color = "gray"
            status = "Pending"

        phase_items.append(ui.ListItem(
            id=phase_id,
            title=f"Phase {phase_id}: {phase_label}",
            badge=ui.Badge(status, color=color),
        ))

    return ui.Stack(children=[
        ui.Text("Analysis in Progress"),
        ui.Progress(value=percent, label=f"{phase_name} ({percent}%)"),
        ui.List(items=phase_items),
    ])


def build_progress_with_controls(progress: dict, case_id,
                                 run: dict | None = None) -> ui.UINode:
    """Progress view + confidence badge + Cancel button (B3).

    `run` is the latest analysis_runs row for the case, or None if not loaded.
    Cancel button is shown whenever the run status is in the active set.
    """
    run = run or {}
    status = (run.get("status") or "").lower()
    run_id = run.get("run_id")
    confidence = run.get("confidence_current")

    _ACTIVE = {"pending", "census", "categorize", "inspect", "summarize",
               "gap_review", "deep", "adversarial", "report"}
    is_active = status in _ACTIVE

    header_children: list = [ui.Text("Analysis in Progress")]
    if run_id is not None:
        header_children.append(ui.Badge(f"Run #{run_id}", color="blue"))
    if confidence is not None:
        header_children.append(ui.Badge(f"Confidence {_pct(confidence)}",
                                        color="gray"))
    if status:
        color = "yellow" if is_active else "green"
        header_children.append(ui.Badge(status.replace("_", " ").title(),
                                        color=color))

    # Progress list (reuse build_progress layout)
    current_phase = str(progress.get("phase", 0))
    phase_name = progress.get("phase_name", "")
    files_done = progress.get("files_done", 0)
    files_total = progress.get("files_total", 0)
    percent = progress.get("percent", 0)

    phase_items = []
    for phase_id, phase_label in PHASES:
        if _phase_num(phase_id) < _phase_num(current_phase):
            color = "green"
            ph_status = "Done"
        elif phase_id == current_phase:
            color = "yellow"
            ph_status = f"{files_done}/{files_total}" if files_total else f"{percent}%"
        else:
            color = "gray"
            ph_status = "Pending"
        phase_items.append(ui.ListItem(
            id=phase_id,
            title=f"Phase {phase_id}: {phase_label}",
            badge=ui.Badge(ph_status, color=color),
        ))

    children: list = [
        ui.Stack(direction="h", gap=2, children=header_children),
        ui.Progress(value=percent, label=f"{phase_name} ({percent}%)"),
        ui.List(items=phase_items),
    ]

    # Gap review nav link
    if status == "gap_review":
        children.append(ui.Alert(
            title="Gap review needed",
            message=("Analysis is paused pending your decision on identified "
                     "gaps. Open the Gap Review tab to continue or request "
                     "more evidence."),
            type="warning",
        ))
        children.append(ui.Button(
            label="Open Gap Review",
            variant="primary",
            icon="ShieldAlert",
            on_click=ui.Call("__panel__dashboard", tab="gap_review",
                             section="", view="", case_id=str(case_id)),
        ))

    # Cancel button
    if is_active:
        children.append(ui.Stack(direction="h", gap=2, children=[
            ui.Button(
                label="Cancel Analysis",
                variant="ghost",
                icon="X",
                on_click=ui.Call("cancel_analysis", case_id=case_id),
            ),
        ]))

    return ui.Stack(children=children, gap=3)


def build_not_started(case_id) -> ui.UINode:
    """No analysis yet — show Run Analysis button."""
    return ui.Stack(children=[
        ui.Text("No Analysis Yet"),
        ui.Text("Upload documents and run analysis to generate a forensic intelligence report."),
        ui.Button(
            label="Run Analysis", variant="primary",
            on_click=ui.Call("run_analysis", case_id=case_id),
        ),
    ])


def build_outdated(case_id, new_files: int, version: str) -> ui.UINode:
    """Analysis exists but outdated — new files since last run."""
    return ui.Stack(children=[
        ui.Alert(
            title="Analysis Outdated",
            message=f"{new_files} new file(s) since v{version}. Current findings may be incomplete.",
            type="warning",
        ),
        ui.Button(
            label="Re-analyze", variant="primary",
            on_click=ui.Call("run_analysis", case_id=case_id),
        ),
        ui.Button(
            label="View Current Report", variant="ghost",
            on_click=ui.Call("__panel__dashboard", tab="report",
                             section=str(case_id), view="", case_id=str(case_id)),
        ),
    ])
