"""Track D (D2+D3+D4) — full app-surface chat tools.

D2 get_report (read), D3 destructive deletes (delete_case/delete_file),
D4 read drill-down (list_case_files, get_case_detail, update_case,
analysis_status, get_intelligence_graph, list_entities, get_entity,
list_relationships, list_timeline_events, get_taxonomy, get_audit_log,
list_analysis_runs).

Federal contract:
- every new chat tool is @require_unlock gated;
- every read declares action_type='read' + data_model=;
- the two delete tools declare action_type='destructive' (kernel auto-inserts
  the confirmation card) + effects= + data_model=;
- get_report no-completed-run returns a SUCCESS fact ({available: false}),
  never an error;
- list tools cap items (<=50) and carry total/has_more so big cases don't
  blow the cache/envelope;
- agency_id threads to every Cases-API call.
"""
import ast
import asyncio
import os
import re
from pathlib import Path

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

_NEW_HANDLER_FILES = ["handlers_control.py", "handlers_drilldown.py",
                      "handlers_intel.py"]

# (name, action_type) for every NEW Track-D tool
_NEW_READ_TOOLS = {
    "get_report",
    "list_case_files", "get_case_detail", "analysis_status",
    "get_intelligence_graph", "list_entities_tool", "get_entity_tool",
    "list_relationships_tool", "list_timeline_events", "get_taxonomy_tool",
    "get_audit_log_tool", "list_analysis_runs",
}
_NEW_DESTRUCTIVE_TOOLS = {"delete_case", "delete_file"}
_NEW_WRITE_TOOLS = {"update_case"}


def _src(name):
    with open(os.path.join(_ROOT, name)) as f:
        return f.read()


# ── decorator-level guards (pure source scan) ────────────────────────────────


def _walk_chat_functions(files):
    for fname in files:
        src = (Path(_ROOT) / fname).read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            for dec in node.decorator_list:
                if not (isinstance(dec, ast.Call)
                        and isinstance(dec.func, ast.Attribute)
                        and dec.func.attr == "function"
                        and isinstance(dec.func.value, ast.Name)
                        and dec.func.value.id == "chat"):
                    continue
                tool_name = None
                if dec.args and isinstance(dec.args[0], ast.Constant):
                    tool_name = dec.args[0].value
                kwargs = {kw.arg: kw.value for kw in dec.keywords}
                yield fname, dec.lineno, node.name, tool_name, kwargs


def _registered_tool_names():
    return {tname for _f, _l, _n, tname, _k in _walk_chat_functions(_NEW_HANDLER_FILES)}


def test_all_new_tools_registered():
    names = _registered_tool_names()
    expected = {
        "get_report",
        "delete_case", "delete_file",
        "list_case_files", "get_case_detail", "update_case",
        "analysis_status", "get_intelligence_graph", "list_entities",
        "get_entity", "list_relationships", "list_timeline_events",
        "get_taxonomy", "get_audit_log", "list_analysis_runs",
    }
    missing = expected - names
    assert not missing, f"Track-D tools not registered: {missing}"


def test_deletes_are_destructive():
    for fname, lineno, fn_name, tname, kwargs in _walk_chat_functions(_NEW_HANDLER_FILES):
        if tname not in ("delete_case", "delete_file"):
            continue
        at = kwargs.get("action_type")
        assert isinstance(at, ast.Constant) and at.value == "destructive", (
            f"{tname} at {fname}:{lineno} must be action_type='destructive' "
            f"(kernel auto-inserts the confirmation card)")
        # destructive tools still need effects + data_model
        assert "effects" in kwargs, f"{tname} missing effects="
        assert "data_model" in kwargs, f"{tname} missing data_model="


def test_reads_declare_data_model_and_action_type():
    read_tools = {"get_report", "list_case_files", "get_case_detail",
                  "analysis_status", "get_intelligence_graph", "list_entities",
                  "get_entity", "list_relationships", "list_timeline_events",
                  "get_taxonomy", "get_audit_log", "list_analysis_runs"}
    seen = set()
    for fname, lineno, fn_name, tname, kwargs in _walk_chat_functions(_NEW_HANDLER_FILES):
        if tname not in read_tools:
            continue
        seen.add(tname)
        at = kwargs.get("action_type")
        assert isinstance(at, ast.Constant) and at.value == "read", (
            f"{tname} at {fname}:{lineno} must be action_type='read'")
        assert "data_model" in kwargs, f"{tname} missing data_model= (V23)"
    assert seen == read_tools, f"missing read tools: {read_tools - seen}"


def test_update_case_is_write_with_effects_and_model():
    for fname, lineno, fn_name, tname, kwargs in _walk_chat_functions(_NEW_HANDLER_FILES):
        if tname != "update_case":
            continue
        at = kwargs.get("action_type")
        assert isinstance(at, ast.Constant) and at.value == "write"
        assert "effects" in kwargs and "data_model" in kwargs
        return
    pytest.fail("update_case not found")


def test_all_new_tools_gated():
    """Every @chat.function in the new files carries @require_unlock."""
    for fname in _NEW_HANDLER_FILES:
        src = _src(fname)
        for m in re.finditer(r"@chat\.function\(", src):
            seg = src[m.start(): src.index("async def", m.start())]
            assert "@require_unlock" in seg, (
                f"{fname}: @chat.function at offset {m.start()} not gated")


def test_new_tools_thread_agency():
    """Every queries.<fn> call in the new handlers carries agency_id=
    (except verify_audit/delete which still take it — all read/write fns
    accept the kwarg)."""
    _FNS = ("get_case", "get_cases", "get_files", "get_analysis",
            "get_latest_active_run", "sign_report_url", "delete_case",
            "update_case", "get_graph", "list_entities", "get_entity",
            "list_relationships", "list_events", "get_taxonomy",
            "get_audit_log", "list_runs", "verify_audit", "delete_file")
    offenders = []
    for fname in _NEW_HANDLER_FILES:
        src = _src(fname)
        for fn in _FNS:
            for m in re.finditer(r"queries\." + fn + r"\(", src):
                depth, i = 0, m.end() - 1
                while i < len(src):
                    if src[i] == "(":
                        depth += 1
                    elif src[i] == ")":
                        depth -= 1
                        if depth == 0:
                            break
                    i += 1
                call = src[m.start():i + 1]
                if "agency_id" not in call:
                    offenders.append(f"{fname}: {call[:80]}")
    assert not offenders, "agency-blind calls:\n" + "\n".join(offenders)


# ── runtime behaviour (with the gate stubbed open) ───────────────────────────


class _User:
    imperal_id = "imp_u_test"
    agency_id = "default"
    email = "u@example.org"


class _Ctx:
    def __init__(self):
        self.user = _User()
        self.cache = None


def _unlock(monkeypatch):
    import auth_gate
    from auth_gate import UnlockState

    async def fake(ctx):
        return UnlockState(unlocked=True, agency_id="default", role="admin")
    monkeypatch.setattr(auth_gate, "_fetch_unlock", fake)


def test_get_report_no_run_is_success_fact(monkeypatch):
    import handlers_control
    import queries
    _unlock(monkeypatch)

    async def no_analysis(case_id, agency_id=None):
        return {"id": case_id, "name": "Alex Case 1", "active_run_id": None}

    async def no_run(case_id, agency_id=None):
        return {}
    monkeypatch.setattr(queries, "get_case", no_analysis)
    monkeypatch.setattr(queries, "get_analysis", no_analysis)
    monkeypatch.setattr(queries, "get_latest_active_run", no_run)

    res = asyncio.run(handlers_control.fn_get_report(
        _Ctx(), handlers_control.GetReportParams(case_id=7)))
    assert res.status == "success", "no-run report is a FACT, not an error"
    assert res.data.get("available") is False
    assert "run analysis" in (res.data.get("reason") or "").lower()


def test_get_report_signs_url(monkeypatch):
    import handlers_control
    import queries
    _unlock(monkeypatch)

    async def case(case_id, agency_id=None):
        return {"id": case_id, "name": "Alex Case 1",
                "active_run_id": 99, "analysis_status": "completed"}

    async def analysis(case_id, agency_id=None):
        return {"status": "completed", "active_run_id": 99}

    async def latest(case_id, agency_id=None):
        return {"run_id": 99, "status": "completed", "version": 3}

    async def sign(case_id, run_id, fmt="pdf", ttl=600, agency_id=None):
        assert run_id == 99 and fmt == "pdf" and ttl == 600
        return {"url": "https://api/report/signed?sig=abc", "expires_in": 600}
    monkeypatch.setattr(queries, "get_case", case)
    monkeypatch.setattr(queries, "get_analysis", analysis)
    monkeypatch.setattr(queries, "get_latest_active_run", latest)
    monkeypatch.setattr(queries, "sign_report_url", sign)

    res = asyncio.run(handlers_control.fn_get_report(
        _Ctx(), handlers_control.GetReportParams(case_id=7)))
    assert res.status == "success"
    assert res.data["url"].startswith("https://")
    assert res.data["format"] == "pdf"
    assert res.data["available"] is True


def test_get_report_prefers_analysis_status_over_case_status(monkeypatch):
    """Precedence guard: /cases/{id}/analysis returns BOTH `status`
    (case lifecycle = 'active') and `analysis_status` (= 'completed').
    get_report must read `analysis_status` FIRST, otherwise it wrongly
    reports 'no completed analysis' for an active case whose analysis is
    finished. Mirrors live case 3812 / run 21522."""
    import handlers_control
    import queries
    _unlock(monkeypatch)

    async def case(case_id, agency_id=None):
        # active_run_id is set; case status is the lifecycle 'active'
        return {"id": case_id, "name": "Case 3812",
                "active_run_id": 21522, "status": "active"}

    async def analysis(case_id, agency_id=None):
        # the precedence trap: status='active' (case) BEFORE
        # analysis_status='completed' (analysis)
        return {"status": "active", "analysis_status": "completed",
                "active_run_id": 21522}

    async def latest(case_id, agency_id=None):
        return {"run_id": 21522, "status": "completed", "version": 1}

    async def sign(case_id, run_id, fmt="pdf", ttl=600, agency_id=None):
        assert run_id == 21522
        return {"url": "https://api/report/signed?sig=xyz", "expires_in": 600}
    monkeypatch.setattr(queries, "get_case", case)
    monkeypatch.setattr(queries, "get_analysis", analysis)
    monkeypatch.setattr(queries, "get_latest_active_run", latest)
    monkeypatch.setattr(queries, "sign_report_url", sign)

    res = asyncio.run(handlers_control.fn_get_report(
        _Ctx(), handlers_control.GetReportParams(case_id=3812)))
    assert res.status == "success"
    assert res.data["available"] is True, (
        "completed analysis on an active case MUST yield a signed report — "
        "analysis_status must win over case status")
    assert res.data["url"].startswith("https://")
    assert res.data["run_id"] == 21522


def test_delete_case_calls_api(monkeypatch):
    import handlers_control
    import queries
    _unlock(monkeypatch)
    calls = {}

    async def case(case_id, agency_id=None):
        return {"id": case_id, "name": "Old Case"}

    async def delete(case_id, agency_id=None):
        calls["deleted"] = (case_id, agency_id)
        return {"deleted": True}
    monkeypatch.setattr(queries, "get_case", case)
    monkeypatch.setattr(queries, "delete_case", delete)

    res = asyncio.run(handlers_control.fn_delete_case(
        _Ctx(), handlers_control.DeleteCaseParams(case_id=12)))
    assert res.status == "success"
    assert res.data["deleted"] is True
    assert calls["deleted"] == (12, "default")


def test_delete_file_calls_api(monkeypatch):
    import handlers_control
    import queries
    _unlock(monkeypatch)
    calls = {}

    async def delete(case_id, file_id, agency_id=None):
        calls["d"] = (case_id, file_id, agency_id)
        return {"deleted": True}
    monkeypatch.setattr(queries, "delete_file", delete)

    res = asyncio.run(handlers_control.fn_delete_file(
        _Ctx(), handlers_control.DeleteFileParams(case_id=12, file_id=88)))
    assert res.status == "success"
    assert res.data["deleted"] is True
    assert calls["d"] == (12, 88, "default")


def test_list_case_files_caps_and_totals(monkeypatch):
    import handlers_drilldown
    import queries
    _unlock(monkeypatch)
    rows = [{"id": i, "filename": f"f{i}.pdf", "size": 10, "mime_type": "application/pdf"}
            for i in range(120)]

    async def files(case_id, agency_id=None):
        return rows
    monkeypatch.setattr(queries, "get_files", files)

    res = asyncio.run(handlers_drilldown.fn_list_case_files(
        _Ctx(), handlers_drilldown.CaseIdParams(case_id=3)))
    assert res.status == "success"
    assert len(res.data["items"]) <= 50, "list must cap at <=50"
    assert res.data["total"] == 120
    assert res.data["has_more"] is True


def test_get_intelligence_graph_summarizes(monkeypatch):
    import handlers_drilldown
    import queries
    _unlock(monkeypatch)

    async def graph(case_id, max_nodes=200, min_mentions=1, agency_id=None):
        return {
            "nodes": [{"data": {"id": str(i), "label": f"E{i}",
                                "mention_count": 100 - i}} for i in range(2655)],
            "edges": [{"data": {"id": f"e{i}", "source": "0", "target": str(i)}}
                      for i in range(5000)],
            "stats": {"total_entities": 2655, "total_edges": 5000},
        }
    monkeypatch.setattr(queries, "get_graph", graph)

    res = asyncio.run(handlers_drilldown.fn_get_intelligence_graph(
        _Ctx(), handlers_drilldown.CaseIdParams(case_id=3)))
    assert res.status == "success"
    # MUST NOT dump 2655 nodes
    top = res.data.get("top_entities") or res.data.get("items") or []
    assert len(top) <= 50, "graph must cap top entities, not dump 2655"
    assert res.data["node_count"] == 2655
    assert res.data["edge_count"] == 5000


def test_list_entities_caps(monkeypatch):
    import handlers_drilldown
    import queries
    _unlock(monkeypatch)
    rows = [{"id": i, "type": "PERSON", "value": f"P{i}", "mention_count": i}
            for i in range(80)]

    async def ents(case_id, limit=50, type_filter=None, min_mentions=0, agency_id=None):
        return rows[:limit]
    monkeypatch.setattr(queries, "list_entities", ents)

    res = asyncio.run(handlers_drilldown.fn_list_entities(
        _Ctx(), handlers_drilldown.ListEntitiesParams(case_id=3)))
    assert res.status == "success"
    assert len(res.data["items"]) <= 50


def test_get_audit_log_is_chain_of_custody(monkeypatch):
    import handlers_intel
    import handlers_drilldown
    import queries
    _unlock(monkeypatch)
    rows = [{"id": i, "event_type": "analysis_started", "actor": "imp_u_x",
             "occurred_at": "2026-06-01T00:00:00"} for i in range(60)]

    async def audit(case_id, limit=50, agency_id=None):
        return rows[:limit]

    async def verify(case_id, agency_id=None):
        # Real Cases API key is chain_valid (not valid/verified)
        return {"chain_valid": True, "total_entries": 60}
    monkeypatch.setattr(queries, "get_audit_log", audit)
    monkeypatch.setattr(queries, "verify_audit", verify)

    res = asyncio.run(handlers_intel.fn_get_audit_log(
        _Ctx(), handlers_drilldown.CaseIdParams(case_id=3)))
    assert res.status == "success"
    assert len(res.data["items"]) <= 50
    assert res.data["verified"] is True
    assert "60 entries checked" in res.summary


def test_update_case_patches(monkeypatch):
    import handlers_control
    import queries
    _unlock(monkeypatch)
    calls = {}

    async def update(case_id, name=None, description=None, agency_id=None):
        calls["u"] = (case_id, name, description, agency_id)
        return {"id": case_id, "name": name or "X"}
    monkeypatch.setattr(queries, "update_case", update)

    res = asyncio.run(handlers_control.fn_update_case(
        _Ctx(), handlers_control.UpdateCaseParams(
            case_id=5, name="Renamed Case")))
    assert res.status == "success"
    assert calls["u"][0] == 5 and calls["u"][1] == "Renamed Case"
    assert calls["u"][3] == "default"
