"""Federal V20/V24: every @chat.function(action_type='write'|'destructive', ...)
MUST declare BOTH effects= (non-empty side-effect surface) AND data_model=.

AST-scans handlers.py + handlers_analysis.py for decorator kwargs — mirrors the
kernel's V20/V24 validators (which are WARN-only at SDK level; this turns the
WARN into a hard local gate so write tools never ship without their declared
effects + typed return contract).

Pure source scan — no extension import, no SDK required. CI-safe. Mirrors
tests/test_read_handlers_declare_data_model.py (the V23 read-side analog).
"""
import ast
from pathlib import Path

_EXT_ROOT = Path(__file__).resolve().parent.parent
_HANDLER_FILES = ["handlers.py", "handlers_analysis.py",
                  "handlers_share.py", "handlers_files.py",
                  "handlers_admin.py"]

_WRITE_ACTION_TYPES = {"write", "destructive"}


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


def _is_write_or_destructive(kwargs):
    v = kwargs.get("action_type")
    return isinstance(v, ast.Constant) and v.value in _WRITE_ACTION_TYPES


def test_every_write_handler_declares_nonempty_effects():
    """V20: write/destructive tools must declare a non-empty effects=[...] list literal."""
    bad = []
    for fname, lineno, fn_name, kwargs in _walk_chat_function_decorators():
        if not _is_write_or_destructive(kwargs):
            continue
        eff = kwargs.get("effects")
        if eff is None:
            bad.append(f"{fname}:{lineno} {fn_name} (no effects=)")
            continue
        if not (isinstance(eff, ast.List) and len(eff.elts) > 0):
            bad.append(f"{fname}:{lineno} {fn_name} (effects= not a non-empty list literal)")
            continue
        # every element must be a non-empty string constant of shape "verb:resource"
        for el in eff.elts:
            if not (isinstance(el, ast.Constant) and isinstance(el.value, str) and ":" in el.value):
                bad.append(f"{fname}:{lineno} {fn_name} (effects entry not a 'verb:resource' string: {ast.dump(el)})")
                break
    assert not bad, "Write/destructive handlers with bad effects=: " + ", ".join(bad)


def test_every_write_handler_declares_data_model():
    """V24: write/destructive tools must declare data_model= (typed return contract)."""
    missing = []
    for fname, lineno, fn_name, kwargs in _walk_chat_function_decorators():
        if not _is_write_or_destructive(kwargs):
            continue
        if "data_model" not in kwargs:
            missing.append(f"{fname}:{lineno} {fn_name}")
    assert not missing, "Write/destructive handlers missing data_model=: " + ", ".join(missing)
