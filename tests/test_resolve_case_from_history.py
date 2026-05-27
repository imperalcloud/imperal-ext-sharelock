"""resolve_case_from_history tests — pronoun follow-up case resolution.

Live trace 2026-05-27 showed case_chat losing context on «о нем» follow-ups
under SDK 5.0+ typed dispatch (the legacy wrapper-LLM history channel is
gone). resolve_case_from_history scans recent USER turns for the most
recently named case so resolve_case_id can recover the case_id.
"""
from __future__ import annotations

from unittest.mock import patch, AsyncMock

import pytest

import case_resolver


_CASES = [
    {"id": 3812, "name": "Test Files"},
    {"id": 35, "name": "Alex Case 1"},
    {"id": 34, "name": "New Investigation"},
    {"id": 33462, "name": "testcase123"},
]


@pytest.mark.asyncio
async def test_history_name_match_resolves_pronoun_follow_up():
    history = [
        {"role": "user", "content": "Покажи мои дела"},
        {"role": "assistant", "content": "Вот список дел..."},
        {"role": "user", "content": "Что с делом Test Files?"},
        {"role": "assistant", "content": "Test Files (ID: 3812) — completed."},
    ]
    with patch.object(case_resolver.queries, "get_cases", new=AsyncMock(return_value=_CASES)):
        cid, path = await case_resolver.resolve_case_from_history("imp_u_test", history)
    assert cid == 3812
    assert path == "history_name"


@pytest.mark.asyncio
async def test_history_picks_most_recent_user_turn():
    history = [
        {"role": "user", "content": "Что с Alex Case 1?"},
        {"role": "assistant", "content": "Alex Case 1 (ID: 35)"},
        {"role": "user", "content": "Что с делом Test Files?"},
        {"role": "assistant", "content": "Test Files (ID: 3812)"},
    ]
    with patch.object(case_resolver.queries, "get_cases", new=AsyncMock(return_value=_CASES)):
        cid, path = await case_resolver.resolve_case_from_history("imp_u_test", history)
    assert cid == 3812, "most recent user turn (Test Files) wins over earlier (Alex Case 1)"
    assert path == "history_name"


@pytest.mark.asyncio
async def test_history_id_match_when_no_name_in_turn():
    history = [
        {"role": "user", "content": "Open case 35"},
        {"role": "assistant", "content": "Alex Case 1 (ID: 35) loaded."},
        {"role": "user", "content": "ok"},
    ]
    with patch.object(case_resolver.queries, "get_cases", new=AsyncMock(return_value=_CASES)):
        cid, path = await case_resolver.resolve_case_from_history("imp_u_test", history)
    assert cid == 35
    assert path == "history_id"


@pytest.mark.asyncio
async def test_history_does_not_scan_assistant_turns():
    """Bot rendered Test Files but user never named it — do NOT pick it up."""
    history = [
        {"role": "user", "content": "Покажи дела"},
        {"role": "assistant", "content": "Test Files (ID: 3812), Alex Case 1 (ID: 35)"},
    ]
    with patch.object(case_resolver.queries, "get_cases", new=AsyncMock(return_value=_CASES)):
        cid, path = await case_resolver.resolve_case_from_history("imp_u_test", history)
    assert cid is None
    assert path is None


@pytest.mark.asyncio
async def test_history_empty_returns_none():
    cid, path = await case_resolver.resolve_case_from_history("imp_u_test", [])
    assert cid is None
    assert path is None


@pytest.mark.asyncio
async def test_history_anthropic_content_blocks():
    """ctx.history may contain Anthropic-format content blocks (list of dicts)."""
    history = [
        {"role": "user", "content": [
            {"type": "text", "text": "Что с делом Test Files?"},
        ]},
    ]
    with patch.object(case_resolver.queries, "get_cases", new=AsyncMock(return_value=_CASES)):
        cid, path = await case_resolver.resolve_case_from_history("imp_u_test", history)
    assert cid == 3812
    assert path == "history_name"


@pytest.mark.asyncio
async def test_resolve_case_id_falls_through_to_history():
    """resolve_case_id should slot history between message and skeleton."""
    history = [
        {"role": "user", "content": "Расскажи о деле Test Files"},
    ]
    with patch.object(case_resolver.queries, "get_cases", new=AsyncMock(return_value=_CASES)):
        cid, path = await case_resolver.resolve_case_id(
            "imp_u_test",
            "Расскажи о нем детально",  # pronoun, no case info
            panel_case_id=None,
            skeleton_case_id=None,
            history=history,
        )
    assert cid == 3812
    assert path == "history_name"


@pytest.mark.asyncio
async def test_resolve_case_id_message_wins_over_history():
    """Current turn name match still beats history."""
    history = [
        {"role": "user", "content": "Расскажи о деле Test Files"},
    ]
    with patch.object(case_resolver.queries, "get_cases", new=AsyncMock(return_value=_CASES)):
        cid, path = await case_resolver.resolve_case_id(
            "imp_u_test",
            "Расскажи о Alex Case 1",  # explicit different case in current turn
            panel_case_id=None,
            skeleton_case_id=None,
            history=history,
        )
    assert cid == 35
    assert path == "name_match"


@pytest.mark.asyncio
async def test_resolve_case_id_history_wins_over_skeleton():
    """History (current focus) beats skeleton (cached hint) when both present."""
    history = [
        {"role": "user", "content": "Расскажи о деле Test Files"},
    ]
    with patch.object(case_resolver.queries, "get_cases", new=AsyncMock(return_value=_CASES)):
        cid, path = await case_resolver.resolve_case_id(
            "imp_u_test",
            "о нем",  # pronoun
            panel_case_id=None,
            skeleton_case_id=35,  # cached
            history=history,
        )
    assert cid == 3812, "history mention overrides stale skeleton hint"
    assert path == "history_name"
