"""Optimal MCP server registers role-scoped tool groups.

Every registered schema rides in each turn's context, so a role carries only
the groups its duties use; unknown roles fail open to the full set (minus the
destructive index-management group, which is dev/test-only).
"""

from __future__ import annotations

import pytest
from roboco.mcp.optimal_server import (
    _RESULT_CONTENT_CAP,
    _cap_result_content,
    create_optimal_mcp_server,
)


async def _tool_names(role: str, monkeypatch: pytest.MonkeyPatch) -> set[str]:
    monkeypatch.delenv("ROBOCO_ALLOW_FULL_TOOLSET", raising=False)
    if role:
        monkeypatch.setenv("ROBOCO_AGENT_ROLE", role)
    else:
        monkeypatch.delenv("ROBOCO_AGENT_ROLE", raising=False)
    server = create_optimal_mcp_server("00000000-0000-0000-0000-000000000042")
    return {t.name for t in await server.list_tools()}


@pytest.mark.asyncio
async def test_developer_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    names = await _tool_names("developer", monkeypatch)
    # Universal + dev-duty groups present.
    assert "roboco_kb_search" in names
    assert "roboco_ask_mentor" in names
    assert "roboco_search_error" in names
    assert "roboco_review_code" in names
    # PM/board decision tools, indexing and destructive admin absent.
    assert "roboco_record_decision" not in names
    assert "roboco_kb_index_code" not in names
    assert "roboco_reindex_all" not in names
    assert "roboco_clear_index" not in names


@pytest.mark.asyncio
async def test_pm_scope_carries_decisions_not_error_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names = await _tool_names("cell_pm", monkeypatch)
    assert "roboco_record_decision" in names
    assert "roboco_search_error" not in names
    assert "roboco_review_code" not in names


@pytest.mark.asyncio
async def test_documenter_carries_indexing(monkeypatch: pytest.MonkeyPatch) -> None:
    names = await _tool_names("documenter", monkeypatch)
    assert "roboco_kb_index_docs" in names
    assert "roboco_get_standards" in names
    assert "roboco_record_decision" not in names


@pytest.mark.asyncio
async def test_unknown_role_fails_open_except_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names = await _tool_names("", monkeypatch)
    assert "roboco_search_error" in names
    assert "roboco_record_decision" in names
    # Destructive index management never registers without the escape hatch.
    assert "roboco_reindex_all" not in names


_ITEM_LIMIT = 2


def test_cap_result_content_caps_text_and_count() -> None:
    items = [
        {"content": "x" * (_RESULT_CONTENT_CAP + 200), "source": "a"},
        {"content": "short", "source": "b"},
        "bare-string-item",
    ]
    capped = _cap_result_content(items, limit=_ITEM_LIMIT)
    assert len(capped) == _ITEM_LIMIT
    assert len(capped[0]["content"]) == _RESULT_CONTENT_CAP + 1  # + ellipsis
    assert capped[0]["content"].endswith("…")
    assert capped[1]["content"] == "short"
    # Original items are not mutated.
    assert len(items[0]["content"]) == _RESULT_CONTENT_CAP + 200


@pytest.mark.asyncio
async def test_full_toolset_escape_hatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROBOCO_AGENT_ROLE", "developer")
    monkeypatch.setenv("ROBOCO_ALLOW_FULL_TOOLSET", "1")
    server = create_optimal_mcp_server("00000000-0000-0000-0000-000000000042")
    names = {t.name for t in await server.list_tools()}
    assert "roboco_reindex_all" in names
    assert "roboco_record_decision" in names
