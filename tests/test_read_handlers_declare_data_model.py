"""V23 federal: every @chat.function(action_type='read', ...) MUST declare data_model=.

AST-scans handlers.py + handlers_analysis.py for decorator kwargs.
Mirrors the kernel's V23 validator behaviour.
"""
import ast
from pathlib import Path

READ_HANDLER_FILES = ["handlers.py", "handlers_analysis.py"]


def _walk_chat_function_decorators():
    """Yield (file, lineno, name, decorator_kwargs) for every @chat.function in the listed files."""
    for fname in READ_HANDLER_FILES:
        src = Path(fname).read_text()
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
