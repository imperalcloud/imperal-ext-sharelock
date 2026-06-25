"""E6 — Delete Case button in the Case Details panel (panels_case.py).

Contract:
- an admin viewing a selected case sees a "Delete case" control in the
  header (danger variant); a plain user does NOT;
- the Delete button does NOT call delete_case directly — it sets
  view="confirm_delete" (mirroring view="create"), so no destructive call
  fires on a single click;
- the confirm view renders a Dialog whose on_confirm calls delete_case with
  the RESOLVED NUMERIC case id (not the folder name), plus a Cancel control
  that returns to the case without deleting;
- a non-admin is never served the confirm view (defense in depth).
"""
import asyncio

import auth_gate
import panels_case as pc


class _User:
    imperal_id = "imp_u_test"
    agency_id = "default"


class _Ctx:
    def __init__(self):
        self.user = _User()
        self.cache = None


def _walk(node):
    yield node
    props = getattr(node, "props", {}) or {}
    for v in props.values():
        if isinstance(v, list):
            for x in v:
                if hasattr(x, "props"):
                    yield from _walk(x)
        elif hasattr(v, "props"):
            yield from _walk(v)


def _find(tree, type_name):
    return [n for n in _walk(tree) if getattr(n, "type", None) == type_name]


def _action_fn(uiaction):
    """The chat/panel function a ui.Call targets ('' if not a Call action)."""
    if uiaction is None:
        return ""
    return (getattr(uiaction, "params", {}) or {}).get("function", "")


def _action_kwargs(uiaction):
    """The kwargs a ui.Call carries (nested under params.params)."""
    if uiaction is None:
        return {}
    return (getattr(uiaction, "params", {}) or {}).get("params", {}) or {}


def _role(monkeypatch, role):
    async def fake(ctx, force_fresh=False):
        return auth_gate.UnlockState(unlocked=True, agency_id="default", role=role)
    # panels_case imported _fetch_unlock by name → patch the module binding.
    monkeypatch.setattr(pc, "_fetch_unlock", fake)
    monkeypatch.setattr(pc, "_agency_consistent", lambda ctx, unlock: True)


def _wire_case(monkeypatch, *, case_id=7, name="Operation Midnight"):
    async def fake_get_api_case(ctx, folder_name):
        return {"id": case_id, "name": name}
    monkeypatch.setattr(pc, "_get_api_case", fake_get_api_case)
    monkeypatch.setattr(pc, "_resolve_api_case_id",
                        lambda api_case: api_case.get("id"))
    # avoid the analysis/upload tab content doing real I/O
    async def fake_upload(ctx, folder):
        return None
    monkeypatch.setattr(pc, "_build_upload_section", fake_upload)


def _delete_buttons(tree):
    out = []
    for b in _find(tree, "Button"):
        oc = (b.props or {}).get("on_click")
        label = (b.props or {}).get("label", "")
        if "Delete" in label or _action_fn(oc) == "delete_case":
            out.append(b)
    return out


def test_admin_sees_delete_control_on_selected_case(monkeypatch):
    _role(monkeypatch, "admin")
    _wire_case(monkeypatch)

    async def fake_tab(ctx, case_id):
        from imperal_sdk import ui
        return ui.Text("analysis content")
    monkeypatch.setattr(pc, "_build_analysis_tab", fake_tab)

    tree = asyncio.run(pc.panel_dashboard(_Ctx(), tab="analysis",
                                          case_id="Operation Midnight"))
    dels = _delete_buttons(tree)
    assert dels, "admin should see a Delete control on a selected case"
    b = dels[0]
    # danger or ghost variant, never primary
    assert b.props.get("variant") in ("danger", "ghost")
    # First click must NOT delete directly — it routes to the confirm view.
    oc = b.props.get("on_click")
    assert _action_fn(oc) == "__panel__dashboard"
    assert _action_kwargs(oc).get("view") == "confirm_delete"


def test_plain_user_has_no_delete_control(monkeypatch):
    _role(monkeypatch, "user")
    _wire_case(monkeypatch)

    async def fake_tab(ctx, case_id):
        from imperal_sdk import ui
        return ui.Text("analysis content")
    monkeypatch.setattr(pc, "_build_analysis_tab", fake_tab)

    tree = asyncio.run(pc.panel_dashboard(_Ctx(), tab="analysis",
                                          case_id="Operation Midnight"))
    assert not _delete_buttons(tree), "plain user must NOT see a Delete control"


def test_confirm_view_calls_delete_case_with_numeric_id(monkeypatch):
    _role(monkeypatch, "admin")
    _wire_case(monkeypatch, case_id=7, name="Operation Midnight")

    tree = asyncio.run(pc.panel_dashboard(_Ctx(), view="confirm_delete",
                                          case_id="Operation Midnight"))
    dialogs = _find(tree, "Dialog")
    assert len(dialogs) == 1, "confirm view must render exactly one Dialog"
    on_confirm = dialogs[0].props.get("on_confirm")
    assert _action_fn(on_confirm) == "delete_case"
    # numeric Cases API id, resolved from the folder name — NOT the folder str
    assert _action_kwargs(on_confirm).get("case_id") == 7
    # a Cancel route back to the case (clears view) must exist
    cancels = [b for b in _find(tree, "Button")
               if _action_fn((b.props or {}).get("on_click")) == "__panel__dashboard"
               and _action_kwargs((b.props or {}).get("on_click")).get("view") == ""]
    assert cancels, "confirm view must offer a Cancel that returns to the case"


def test_plain_user_cannot_reach_confirm_view(monkeypatch):
    _role(monkeypatch, "user")
    _wire_case(monkeypatch)

    tree = asyncio.run(pc.panel_dashboard(_Ctx(), view="confirm_delete",
                                          case_id="Operation Midnight"))
    assert not _find(tree, "Dialog"), "non-admin must never reach the confirm Dialog"
    # and certainly no delete_case action anywhere
    assert not any(_action_fn((b.props or {}).get("on_click")) == "delete_case"
                   for b in _find(tree, "Button"))
