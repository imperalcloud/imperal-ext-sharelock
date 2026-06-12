"""
Sharelock v2 — Share tab builder (right panel, Track C.2 T3).

Renders the case owner + share grants, a share form, and per-grant revoke
buttons. ``case_id`` rides the share form via DForm ``defaults`` — the
deployed renderer seeds its submit-values state from ``props.defaults``
(verified against /opt/imperal-panel DForm.tsx, 2026-06-12), so the POSTed
``params`` carry ``case_id`` alongside the typed ``colleague`` field.
"""
import logging

from imperal_sdk import ui

from app import _user_agency
import queries

log = logging.getLogger("sharelock-v2.panels_share")


async def build_share_tab(ctx, case_id: int):
    """Share tab for a resolved Cases API case id."""
    agency = _user_agency(ctx)
    try:
        data = await queries.get_shares(case_id, agency_id=agency)
    except Exception as exc:
        log.warning(f"share tab: get_shares failed for case {case_id}: {exc}")
        return ui.Alert(title="Shares Unavailable",
                        message="Could not load share grants. Try again shortly.",
                        type="warning")

    owner = data.get("owner") or {}
    shares = [s for s in (data.get("shares") or []) if isinstance(s, dict)]

    owner_label = (owner.get("email") or owner.get("name")
                   or owner.get("imperal_id") or "unknown")
    owner_section = ui.Section(title="Owner", children=[
        ui.KeyValue(items=[
            {"key": "Owner", "value": owner_label},
            {"key": "Imperal ID", "value": owner.get("imperal_id") or "—"},
        ]),
    ])

    rows = []
    for s in shares:
        sid = s.get("imperal_id") or ""
        label = s.get("email") or s.get("name") or sid or "?"
        sub = sid if label != sid else (
            f"granted by {s.get('granted_by')}" if s.get("granted_by") else "")
        left_children = [ui.Text(label)]
        if sub:
            left_children.append(ui.Text(sub))
        rows.append(ui.Row(children=[
            ui.Stack(children=left_children, gap=0),
            ui.Button("Revoke", variant="ghost", size="sm", icon="UserMinus",
                      on_click=ui.Call("unshare_case",
                                       case_id=case_id, colleague=sid)),
        ]))
    shares_section = ui.Section(
        title=f"Shared with ({len(shares)})",
        children=rows if rows else [ui.Text("Not shared with anyone yet.")],
    )

    form_section = ui.Section(title="Share with a colleague", children=[
        ui.Form(
            action="share_case",
            submit_label="Share",
            # case_id rides the submit via DForm defaults (seeds the
            # values dict the form POSTs as params).
            defaults={"case_id": case_id},
            children=[
                ui.Input(param_name="colleague",
                         placeholder="imp_u_... of your colleague"),
            ],
        ),
        ui.Text("Email lookup lands later — use the colleague's imperal id "
                "(shown above for existing grants)."),
    ])

    return ui.Stack(children=[owner_section, shares_section, form_section])
