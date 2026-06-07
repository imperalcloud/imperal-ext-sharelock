import pytest
import intelligence_context as ic


@pytest.mark.asyncio
async def test_fetch_grounded_context_threads_agency_id(monkeypatch):
    """Every Cases API read in the grounded path must carry the agency_id."""
    seen = {}

    def _spy(name, ret):
        async def _fn(*args, **kwargs):
            seen.setdefault(name, []).append(kwargs.get("agency_id", "MISSING"))
            return ret
        return _fn

    monkeypatch.setattr(ic.queries, "get_case", _spy("get_case", {"id": 3812, "active_run_id": 21500}))
    monkeypatch.setattr(ic.queries, "get_run", _spy("get_run", {"run_id": 21500}))
    monkeypatch.setattr(ic.queries, "list_gaps", _spy("list_gaps", []))
    monkeypatch.setattr(ic.queries, "list_summaries", _spy("list_summaries", []))
    monkeypatch.setattr(ic.queries, "list_entities", _spy("list_entities", []))
    monkeypatch.setattr(ic.queries, "get_graph", _spy("get_graph", {}))
    monkeypatch.setattr(ic.queries, "get_taxonomy", _spy("get_taxonomy", []))
    monkeypatch.setattr(ic.queries, "get_audit_log", _spy("get_audit_log", []))
    monkeypatch.setattr(ic.queries, "list_runs", _spy("list_runs", []))
    monkeypatch.setattr(ic.queries, "list_inspections", _spy("list_inspections", []))

    await ic.fetch_grounded_context(3812, agency_id="acme")

    # Every recorded call saw agency_id="acme" — none "MISSING".
    flat = [v for vals in seen.values() for v in vals]
    assert flat, "no queries were called"
    assert all(v == "acme" for v in flat), f"un-scoped reads: {seen}"
