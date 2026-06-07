import json
import pytest
import chat as chat_engine
from imperal_sdk.testing import MockContext

_CTX_DATA = {
    "case": {"id": 3812, "name": "Test Files", "analysis_status": "completed", "active_run_id": 21500},
    "run": {"run_id": 21500},
    "summaries": [
        {"category": "_indictment", "summary_json": {
            "case_theory": "Nicholas Mitchell ... scheme to defraud Manuel Serrano.",
            "target_subjects": [{"name": "Nicholas Mitchell", "role": "principal", "evidence_summary": "x"}],
            "candidate_charges": [{"charge_code": "18 U.S.C. 1343", "charge_title": "Wire Fraud", "target_subject": "Nicholas Mitchell"}],
        }},
    ],
    "gaps": [], "entities": [], "graph_stats": {}, "taxonomy": [], "audit": [],
    "inspections": {}, "runs_history": [],
}


def _ctx_acme():
    ctx = MockContext(user_id="u1")
    ctx.user = ctx.user.model_copy(update={"agency_id": "acme"})
    return ctx


@pytest.mark.asyncio
async def test_run_intelligence_uses_ctx_ai_and_returns_prose(monkeypatch):
    async def _fake_fetch(case_id, agency_id=None):
        assert agency_id == "acme"   # agency threaded from ctx
        return dict(_CTX_DATA)
    monkeypatch.setattr(chat_engine, "fetch_grounded_context", _fake_fetch)

    ctx = _ctx_acme()
    ctx.ai.set_response("CASE CONTEXT", json.dumps(
        {"prose": "Главный обвиняемый — Nicholas Mitchell (ITC Ventures LLC).",
         "claims": [], "confidence": "HIGH", "unknown_fields": []}))

    out = await chat_engine.run_intelligence(
        "кто забуровил?", [], _CTX_DATA["case"], 3812, ctx)
    assert "Nicholas Mitchell" in out


@pytest.mark.asyncio
async def test_run_intelligence_falls_back_to_deterministic_on_garbage(monkeypatch):
    async def _fake_fetch(case_id, agency_id=None):
        return dict(_CTX_DATA)
    monkeypatch.setattr(chat_engine, "fetch_grounded_context", _fake_fetch)

    ctx = _ctx_acme()
    ctx.ai.set_response("CASE CONTEXT", "Sorry, I cannot help with that.")  # non-JSON

    out = await chat_engine.run_intelligence("кто забуровил?", [], _CTX_DATA["case"], 3812, ctx)
    assert "Nicholas Mitchell" in out          # deterministic fallback surfaced the indictment
    assert "cannot help" not in out


@pytest.mark.asyncio
async def test_run_intelligence_falls_back_on_empty_prose(monkeypatch):
    import json
    async def _fake_fetch(case_id, agency_id=None):
        return dict(_CTX_DATA)
    monkeypatch.setattr(chat_engine, "fetch_grounded_context", _fake_fetch)
    ctx = _ctx_acme()
    # valid JSON but empty prose -> must NOT dead-end; deterministic fallback fires
    ctx.ai.set_response("CASE CONTEXT", json.dumps({"prose": "", "claims": [], "confidence": "UNKNOWN", "unknown_fields": []}))
    out = await chat_engine.run_intelligence("кто забуровил?", [], _CTX_DATA["case"], 3812, ctx)
    assert out.strip() != ""
    assert "Nicholas Mitchell" in out
