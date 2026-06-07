import pytest
import chat as chat_engine
from imperal_sdk.testing import MockContext


@pytest.mark.asyncio
async def test_run_intake_uses_ctx_ai():
    ctx = MockContext(user_id="u1")
    ctx.ai.set_response("CASE CONTEXT", "Upload documents to begin analysis.")
    out = await chat_engine.run_intake(
        "как начать?", [], {"cases": [], "case_name": "C1", "analysis_status": None, "file_count": 0}, 7, ctx)
    assert "Upload documents" in out
