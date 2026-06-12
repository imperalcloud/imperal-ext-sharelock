import os


def _src(name):
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, name)) as f:
        return f.read()


def test_no_get_llm_provider_anywhere():
    for mod in ("app.py", "chat.py"):
        s = _src(mod)
        assert "get_llm_provider" not in s, f"{mod} still references get_llm_provider"
        assert "create_message" not in s, f"{mod} still calls create_message"


def test_version_bumped():
    assert 'version="3.7.0"' in _src("app.py")
