"""
Sharelock v2 — Share tab builder (right panel).

Share model (2026-07-14): a case is shared with a SHARELOCK USER (account in
your agency), resolved from the colleague's Sharelock email. The tab shows
the owner, who currently has access (+ Revoke), and a colleague picker.

``case_id`` rides the share form via DForm ``defaults`` — the deployed
renderer seeds its submit-values state from ``props.defaults`` (verified
against /opt/imperal-panel DForm.tsx), so the POSTed ``params`` carry
``case_id`` alongside the chosen ``colleague``.
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
        return ui.Alert(title="Sharing unavailable",
                        message="Could not load sharing for this case. Try again shortly.",
                        type="warning")

    owner = data.get("owner") or {}
    shares = [s for s in (data.get("shares") or []) if isinstance(s, dict)]

    owner_label = owner.get("email") or owner.get("name") or "unknown"
    owner_section = ui.Section(title="Owner", children=[
        ui.Text(f"{owner_label} created this case and always has access."),
    ])

    # ── Who currently has access (+ Revoke) ──────────────────────────────
    shared_ids = set()
    rows = []
    for s in shares:
        suid = s.get("shared_with_user_id")
        shared_ids.add(suid)
        label = (s.get("email") or s.get("name")
                 or s.get("imperal_id") or f"user #{suid}")
        rows.append(ui.Row(children=[
            ui.Text(label),
            ui.Button("Revoke", variant="ghost", size="sm", icon="UserMinus",
                      on_click=ui.Call("unshare_case", case_id=case_id,
                                       colleague=str(suid))),
        ]))
    shares_section = ui.Section(
        title=f"Shared with ({len(shares)})",
        children=rows if rows else [
            ui.Text("Not shared with anyone yet — this case is private to its owner.")],
    )

    # ── Share with a colleague ────────────────────────────────────────────
    # Prefer a picker of agency colleagues (nicer + no typos); fall back to a
    # plain email field if the roster is unavailable.
    colleagues = []
    try:
        users = await queries.list_sharelock_users(agency)
        owner_email = (owner.get("email") or "").lower()
        for u in (users or []):
            if not isinstance(u, dict) or not u.get("is_active"):
                continue
            email = u.get("email") or ""
            if not email or email.lower() == owner_email:
                continue
            if u.get("id") in shared_ids:
                continue
            name = u.get("name")
            colleagues.append({
                "value": email,
                "label": f"{name} ({email})" if name and name != email else email,
            })
    except Exception as exc:
        log.debug(f"share tab: colleague roster unavailable: {exc}")
        colleagues = []

    if colleagues:
        picker = ui.Select(param_name="colleague",
                           placeholder="Choose a colleague…", options=colleagues)
        helper = ("They get read access to this case. Only active Sharelock "
                  "users in your agency appear here.")
    else:
        picker = ui.Input(param_name="colleague",
                          placeholder="Colleague's Sharelock email")
        helper = ("Enter the email your colleague signs in to Sharelock with. "
                  "They must already have a Sharelock account in your agency "
                  "(register or assign them first).")

    form_section = ui.Section(title="Share with a colleague", children=[
        ui.Form(
            action="share_case",
            submit_label="Share",
            defaults={"case_id": case_id},
            children=[picker],
        ),
        ui.Text(helper),
    ])

    return ui.Stack(children=[owner_section, shares_section, form_section])
