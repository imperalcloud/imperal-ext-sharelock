import os


def _prompt():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "prompts", "intelligence.txt")) as f:
        return f.read()


def test_prompt_is_json_protocol_not_tool_call():
    p = _prompt()
    assert "emit_intelligence_response" not in p          # no forced tool call
    assert "JSON" in p                                    # JSON protocol present
    assert "MUST surface" in p or "must surface" in p     # grounded-answer rule


def test_prompt_keeps_four_fields_and_no_emoji_rule():
    p = _prompt()
    for f in ("prose", "claims", "confidence", "unknown_fields"):
        assert f in p
