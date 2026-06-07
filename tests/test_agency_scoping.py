import pytest
import intelligence_context as ic
import skeleton as sk
from imperal_sdk.testing import MockContext

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
    monkeypatch.setattr(ic.queries, "list_runs", _spy("list_runs", [
        {"run_id": 21499, "status": "completed"},
    ]))
    monkeypatch.setattr(ic.queries, "list_inspections", _spy("list_inspections", []))

    await ic.fetch_grounded_context(3812, agency_id="acme")

    # Every recorded call saw agency_id="acme" — none "MISSING".
    flat = [v for vals in seen.values() for v in vals]
    assert flat, "no queries were called"
    assert all(v == "acme" for v in flat), f"un-scoped reads: {seen}"


@pytest.mark.asyncio
async def test_skeleton_refresh_threads_agency_id(monkeypatch):
    seen = []

    async def _get_cases(user_id, agency_id=None):
        seen.append(agency_id)
        return [{"id": 1, "name": "C1", "status": "active"}]

    async def _get_analysis(cid, agency_id=None):
        seen.append(agency_id); return {"analysis_status": "completed"}

    async def _get_files(cid, agency_id=None):
        seen.append(agency_id); return []

    monkeypatch.setattr(sk.queries, "get_cases", _get_cases)
    monkeypatch.setattr(sk.queries, "get_analysis", _get_analysis)
    monkeypatch.setattr(sk.queries, "get_files", _get_files)

    ctx = MockContext(user_id="u1")
    ctx.user = ctx.user.model_copy(update={"agency_id": "acme"})
    await sk.on_skeleton_refresh(ctx)

    assert seen, "no queries called"
    assert all(a == "acme" for a in seen), f"un-scoped skeleton reads: {seen}"
    assert len(seen) == 4, f"expected 4 agency-scoped skeleton reads (get_cases + get_analysis + 2x get_files), got {len(seen)}: {seen}"
