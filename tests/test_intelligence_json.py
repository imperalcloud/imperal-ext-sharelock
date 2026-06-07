from intelligence_response import parse_intelligence_json, build_intelligence_json_instruction


def test_parse_plain_json():
    text = '{"prose": "Nicholas Mitchell — главный обвиняемый.", "claims": [{"text": "x", "sources": [{"family": "I", "num": "2", "subtype": "T", "subnum": "1"}]}], "confidence": "HIGH", "unknown_fields": []}'
    r = parse_intelligence_json(text)
    assert r is not None
    assert "Nicholas Mitchell" in r.prose
    assert r.confidence == "HIGH"
    assert len(r.claims) == 1 and r.claims[0].sources[0].family == "I"


def test_parse_fenced_json():
    text = '```json\n{"prose": "ok", "claims": [], "confidence": "LOW", "unknown_fields": []}\n```'
    r = parse_intelligence_json(text)
    assert r is not None and r.prose == "ok" and r.confidence == "LOW"


def test_parse_garbage_returns_none():
    assert parse_intelligence_json("I cannot help with that.") is None
    assert parse_intelligence_json("") is None
    assert parse_intelligence_json(None) is None


def test_parse_blank_prose_returns_none():
    import json as _json
    assert parse_intelligence_json(_json.dumps({"prose": "", "claims": [], "confidence": "UNKNOWN", "unknown_fields": []})) is None
    assert parse_intelligence_json(_json.dumps({"prose": "   ", "claims": [], "confidence": "UNKNOWN", "unknown_fields": []})) is None


def test_json_instruction_mentions_fields():
    instr = build_intelligence_json_instruction()
    for f in ("prose", "claims", "confidence", "unknown_fields"):
        assert f in instr
