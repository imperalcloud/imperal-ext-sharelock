"""V23 federal: every @chat.function(action_type='read', ...) MUST declare data_model=.

AST-scans handlers.py + handlers_analysis.py for decorator kwargs.
Mirrors the kernel's V23 validator behaviour.
"""
import ast
from pathlib import Path

_EXT_ROOT = Path(__file__).resolve().parent.parent
_HANDLER_FILES = ["handlers.py", "handlers_analysis.py"]


def _walk_chat_function_decorators():
    """Yield (file, lineno, name, decorator_kwargs) for every @chat.function in the listed files."""
    for fname in _HANDLER_FILES:
        src = (_EXT_ROOT / fname).read_text()
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
                kwargs = {kw.arg: kw.value for kw in dec.keywords}
                yield fname, dec.lineno, node.name, kwargs


def _is_read(kwargs):
    v = kwargs.get("action_type")
    return isinstance(v, ast.Constant) and v.value == "read"


def test_every_read_handler_has_data_model():
    missing = []
    for fname, lineno, fn_name, kwargs in _walk_chat_function_decorators():
        if not _is_read(kwargs):
            continue
        if "data_model" not in kwargs:
            missing.append(f"{fname}:{lineno} {fn_name}")
    assert not missing, "Read handlers missing data_model=: " + ", ".join(missing)


def test_case_chat_chain_callable_true():
    """case_chat must be chain_callable=True.

    Original v3.2.0 design set chain_callable=False to keep case_chat on the
    wrapper-LLM path that historically preserved ctx.history. Live trace
    2026-05-27 (post-deploy) showed this assumption was wrong for SDK 5.0+:
    the wrapper-LLM fallback no longer exists, and _resolve_typed_dispatch
    returning None for chain_callable=False causes kernel disambiguation to
    pick the wrong tool — user turn «Расскажи о нем детально» misrouted to
    create_case. Setting chain_callable=True keeps the typed-dispatch path
    intact; resolve_case_id is deterministic and chat_engine degrades
    gracefully if ctx.history is empty.
    """
    import pytest
    for fname, lineno, fn_name, kwargs in _walk_chat_function_decorators():
        if fn_name != "case_chat":
            continue
        cc = kwargs.get("chain_callable")
        assert cc is not None, (
            f"case_chat at {fname}:{lineno} must explicitly set chain_callable=True"
        )
        assert isinstance(cc, ast.Constant) and cc.value is True, (
            f"case_chat at {fname}:{lineno} chain_callable must be True, "
            f"got {ast.dump(cc)}"
        )
        return
    pytest.fail("case_chat handler not found in handlers.py")
