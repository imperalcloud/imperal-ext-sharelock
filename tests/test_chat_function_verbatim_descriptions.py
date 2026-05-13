"""
Federal invariant gate: I-CHAT-FUNCTION-VERBATIM-PARAMS.

Source: imperal/webbee/docs/conventions.md:2807.

When an extension uses the legacy `ChatExtension(tool_name="tool_*_chat", ...)`
wrapper (single-tool model that runs its own LLM tool-use loop), each
`@chat.function`'s decorator description AND each Pydantic param Field
description for a *free-text user-supplied string* parameter MUST contain
the word "verbatim" (case-insensitive). The wrapper LLM otherwise rephrases
user input (RU→EN, drops proper nouns) and downstream deterministic
resolvers (case-id matching, document substring search, case-name dedupe)
silently fail.

This test AST-scans `handlers.py` and `handlers_analysis.py` to enforce the
invariant. It runs purely on the source files — no extension import, no
SDK required. CI-safe.
"""
from __future__ import annotations

import ast
import os
from typing import Iterable

_HERE = os.path.dirname(os.path.abspath(__file__))
_EXT_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
_HANDLER_FILES = (
    os.path.join(_EXT_ROOT, "handlers.py"),
    os.path.join(_EXT_ROOT, "handlers_analysis.py"),
)
_VERBATIM = "verbatim"


def _is_str_annotation(node: ast.expr | None) -> bool:
    """Return True if a Pydantic field annotation is a plain or optional str."""
    if node is None:
        return False
    if isinstance(node, ast.Name) and node.id == "str":
        return True
    if isinstance(node, ast.Subscript):
        inner = ast.unparse(node).replace(" ", "")
        return inner in {"Optional[str]", "Union[str,None]", "Union[None,str]"}
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left = ast.unparse(node.left).strip()
        right = ast.unparse(node.right).strip()
        return {left, right} == {"str", "None"}
    return False


def _str_value(node: ast.expr | None) -> str | None:
    """Extract a plain-string description literal.

    Supports `"..."` constants and f-string-style `JoinedStr` nodes whose
    parts are all string constants. Anything else returns None — the test
    treats absence-of-text as a violation, which is the safe default.
    """
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value for part in node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
    return None


def _field_call_description(call: ast.Call) -> str | None:
    """Extract the `description=` kwarg from a `Field(...)` call."""
    if not (isinstance(call.func, ast.Name) and call.func.id == "Field"):
        return None
    for kw in call.keywords:
        if kw.arg == "description":
            return _str_value(kw.value)
    return None


def _basemodel_str_fields(module: ast.Module) -> dict[str, dict[str, str | None]]:
    """Map BaseModel subclass name -> {field_name: field_description_for_str_fields}."""
    out: dict[str, dict[str, str | None]] = {}
    for cls in (n for n in ast.walk(module) if isinstance(n, ast.ClassDef)):
        bases = {ast.unparse(b) for b in cls.bases}
        if not bases & {"BaseModel"}:
            continue
        fields: dict[str, str | None] = {}
        for stmt in cls.body:
            if not isinstance(stmt, ast.AnnAssign):
                continue
            if not isinstance(stmt.target, ast.Name):
                continue
            if not _is_str_annotation(stmt.annotation):
                continue
            desc = None
            if isinstance(stmt.value, ast.Call):
                desc = _field_call_description(stmt.value)
            fields[stmt.target.id] = desc
        if fields:
            out[cls.name] = fields
    return out


def _chat_function_calls(
    module: ast.Module,
) -> Iterable[tuple[ast.FunctionDef | ast.AsyncFunctionDef, ast.Call]]:
    """Yield (func_def, chat.function_decorator_call) pairs."""
    for node in ast.walk(module):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            if not isinstance(dec.func, ast.Attribute):
                continue
            if dec.func.attr != "function":
                continue
            if not (isinstance(dec.func.value, ast.Name) and dec.func.value.id == "chat"):
                continue
            yield node, dec


def _decorator_description(call: ast.Call) -> str | None:
    for kw in call.keywords:
        if kw.arg == "description":
            return _str_value(kw.value)
    return None


def _param_model_name(func: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """Return the Pydantic params model annotation, e.g. `CreateCaseParams`."""
    for arg in func.args.args:
        if arg.arg in ("self", "ctx"):
            continue
        if arg.annotation is None:
            return None
        return ast.unparse(arg.annotation)
    return None


def _has_verbatim(text: str | None) -> bool:
    return text is not None and _VERBATIM in text.lower()


def _parse(path: str) -> ast.Module:
    with open(path, "r", encoding="utf-8") as f:
        return ast.parse(f.read(), filename=path)


def test_verbatim_invariant_handlers_files() -> None:
    """Every @chat.function with a Pydantic str param must carry 'verbatim' on
    the decorator description AND on the str Field description.
    """
    failures: list[str] = []
    for path in _HANDLER_FILES:
        module = _parse(path)
        models = _basemodel_str_fields(module)
        for func, dec in _chat_function_calls(module):
            model_name = _param_model_name(func)
            if model_name not in models:
                # No Pydantic model with str fields -> invariant does not apply.
                continue
            str_fields = models[model_name]
            if not str_fields:
                continue
            dec_desc = _decorator_description(dec)
            if not _has_verbatim(dec_desc):
                failures.append(
                    f"{os.path.basename(path)}::{func.name} — decorator "
                    f"description missing 'verbatim' "
                    f"(I-CHAT-FUNCTION-VERBATIM-PARAMS): got {dec_desc!r}"
                )
            for field_name, field_desc in str_fields.items():
                if not _has_verbatim(field_desc):
                    failures.append(
                        f"{os.path.basename(path)}::{model_name}.{field_name} — "
                        f"Field description missing 'verbatim' "
                        f"(I-CHAT-FUNCTION-VERBATIM-PARAMS): got {field_desc!r}"
                    )
    assert not failures, (
        "I-CHAT-FUNCTION-VERBATIM-PARAMS violations:\n" + "\n".join(failures)
    )


def test_system_prompt_contains_verbatim_catch_all() -> None:
    """The wrapper system_prompt must carry a catch-all verbatim rule so the
    wrapper LLM applies the discipline even on free-text params we haven't
    flagged individually.
    """
    sp_path = os.path.join(_EXT_ROOT, "system_prompt.txt")
    with open(sp_path, "r", encoding="utf-8") as f:
        text = f.read()
    assert _VERBATIM in text.lower(), (
        "system_prompt.txt missing 'verbatim' catch-all rule "
        "(I-CHAT-FUNCTION-VERBATIM-PARAMS)"
    )
    assert "I-CHAT-FUNCTION-VERBATIM-PARAMS" in text, (
        "system_prompt.txt should reference the federal invariant ID by name "
        "so future readers can find the source-of-truth doc."
    )
