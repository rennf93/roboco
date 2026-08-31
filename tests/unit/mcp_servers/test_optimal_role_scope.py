"""Optimal MCP server registers role-scoped tool groups.

Every registered schema rides in each turn's context, so a role carries only
the groups its duties use; unknown roles fail open to the full set (minus the
destructive index-management group, which is dev/test-only).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from roboco.mcp.optimal_server import (
    _LIVE_WRITE_CAVEAT,
    _RESULT_CONTENT_CAP,
    _append_live_write_caveat,
    _cap_result_content,
    create_optimal_mcp_server,
)
from roboco.mcp.utils import ApiClient


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
    items: list[Any] = [
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


def test_append_live_write_caveat_marks_live_write_hits_only() -> None:
    """A hit whose metadata says provenance=live_write gets the caveat line
    appended to its content; a repo_tree hit (or one with no provenance key
    at all, e.g. a non-DOCUMENTATION index) passes through byte-for-byte."""
    items: list[Any] = [
        {
            "content": "the /v2/widgets endpoint accepts a `color` field",
            "source": "roboco://docs/x.md",
            "metadata": {"provenance": "live_write", "task_id": "t-1"},
        },
        {
            "content": "the /v1/widgets endpoint is stable",
            "source": "roboco://docs/y.md",
            "metadata": {"provenance": "repo_tree"},
        },
        {
            "content": "a learning with no provenance concept at all",
            "source": "roboco://learnings/z",
            "metadata": {"category": "pattern"},
        },
        "bare-string-item",
    ]

    out = _append_live_write_caveat(items)

    assert out[0]["content"].endswith(_LIVE_WRITE_CAVEAT)
    assert out[0]["content"].startswith("the /v2/widgets endpoint")
    assert out[1]["content"] == "the /v1/widgets endpoint is stable"
    assert _LIVE_WRITE_CAVEAT not in out[1]["content"]
    assert out[2]["content"] == "a learning with no provenance concept at all"
    assert out[3] == "bare-string-item"
    # Original items are not mutated.
    assert _LIVE_WRITE_CAVEAT not in items[0]["content"]


def test_append_live_write_caveat_survives_content_cap() -> None:
    """Applied after _cap_result_content (the order roboco_kb_search uses),
    the caveat must still be visible — capping must never eat it."""
    long_content = "x" * (_RESULT_CONTENT_CAP + 200)
    items: list[Any] = [
        {
            "content": long_content,
            "source": "roboco://docs/x.md",
            "metadata": {"provenance": "live_write"},
        },
    ]

    capped = _cap_result_content(items)
    out = _append_live_write_caveat(capped)

    assert out[0]["content"].endswith(_LIVE_WRITE_CAVEAT)


@pytest.mark.asyncio
async def test_full_toolset_escape_hatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROBOCO_AGENT_ROLE", "developer")
    monkeypatch.setenv("ROBOCO_ALLOW_FULL_TOOLSET", "1")
    server = create_optimal_mcp_server("00000000-0000-0000-0000-000000000042")
    names = {t.name for t in await server.list_tools()}
    assert "roboco_reindex_all" in names
    assert "roboco_record_decision" in names


# ---------------------------------------------------------------------------
# Caveat reach: roboco_ask_mentor's `sources` and roboco_rag_query's
# `citations` are the same SearchResultResponse shape roboco_kb_search's
# `results` use (content/source/score/index_type/metadata — confirmed in
# roboco/api/schemas/optimal.py), so _append_live_write_caveat wires in
# unchanged. This exercises the real tool bodies end-to-end (via
# MCPServer.call_tool), not just the shared helper in isolation above.
# ---------------------------------------------------------------------------

_LIVE_WRITE_ITEM: dict[str, Any] = {
    "content": "the /v2/widgets endpoint accepts a `color` field",
    "source": "roboco://docs/x.md",
    "score": 0.9,
    "index_type": "documentation",
    "metadata": {"provenance": "live_write", "task_id": "t-1"},
}


class _FakeApiResponse:
    """Stands in for mcp.utils.ApiResponse: .ok is True, .json() is fixed."""

    ok = True

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


async def _call_tool(
    role: str,
    tool_name: str,
    arguments: dict[str, Any],
    api_payload: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Register the role's tools, call one by name with ApiClient.post
    stubbed to return ``api_payload``, and return the structured result."""
    monkeypatch.delenv("ROBOCO_ALLOW_FULL_TOOLSET", raising=False)
    monkeypatch.setenv("ROBOCO_AGENT_ROLE", role)
    server = create_optimal_mcp_server("00000000-0000-0000-0000-000000000042")
    with patch.object(
        ApiClient, "post", new=AsyncMock(return_value=_FakeApiResponse(api_payload))
    ):
        # mcp 1.29 (the lockfile pin) returns a tuple (unstructured,
        # structured_content) whose second element carries the handler's
        # dict payload (None when the tool errored); mcp 2.x instead returns
        # a CallToolResult object. Unwrap both shapes.
        raw: Any = await server.call_tool(tool_name, arguments)
    structured = raw[1] if isinstance(raw, tuple) else raw.structured_content
    assert isinstance(structured, dict)
    return structured


@pytest.mark.asyncio
async def test_ask_mentor_caveats_live_write_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await _call_tool(
        "developer",
        "roboco_ask_mentor",
        {"question": "how does the widgets API work?"},
        {
            "answer": "…",
            "sources": [_LIVE_WRITE_ITEM],
            "conversation_id": "c-1",
            "suggested_followups": [],
        },
        monkeypatch,
    )
    assert result["sources"][0]["content"].endswith(_LIVE_WRITE_CAVEAT)


@pytest.mark.asyncio
async def test_rag_query_caveats_live_write_citations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await _call_tool(
        "developer",
        "roboco_rag_query",
        {"query": "how does the widgets API work?"},
        {
            "answer": "…",
            "citations": [_LIVE_WRITE_ITEM],
            "context_used": 1,
        },
        monkeypatch,
    )
    assert result["citations"][0]["content"].endswith(_LIVE_WRITE_CAVEAT)


# ---------------------------------------------------------------------------
# Post-flip contract: the provenance flip (live_write -> repo_tree when the
# writing task's root chain reaches terminal completed) must make the caveat
# disappear at every entry point, because the append is gated purely on
# metadata.provenance. Each test below feeds the SAME hit to the real tool
# body first with provenance=repo_tree (no caveat, content byte-for-byte)
# then with provenance=live_write (caveat appended), seeding the value
# directly in the mocked API response.
# ---------------------------------------------------------------------------

_REPO_TREE_ITEM = {
    **_LIVE_WRITE_ITEM,
    "metadata": {**_LIVE_WRITE_ITEM["metadata"], "provenance": "repo_tree"},
}


async def _assert_caveat_gates_on_provenance(
    tool_name: str,
    tool_arguments: dict[str, Any],
    api_payload_builder: Any,
    results_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Call ``tool_name`` once with a repo_tree hit and once with the same
    hit marked live_write; assert the caveat tracks the provenance value."""
    repo_tree_result = await _call_tool(
        "developer",
        tool_name,
        tool_arguments,
        api_payload_builder([_REPO_TREE_ITEM]),
        monkeypatch,
    )
    assert _LIVE_WRITE_CAVEAT not in repo_tree_result[results_key][0]["content"]
    assert (
        repo_tree_result[results_key][0]["content"]
        == "the /v2/widgets endpoint accepts a `color` field"
    )

    live_write_result = await _call_tool(
        "developer",
        tool_name,
        tool_arguments,
        api_payload_builder([_LIVE_WRITE_ITEM]),
        monkeypatch,
    )
    assert live_write_result[results_key][0]["content"].endswith(_LIVE_WRITE_CAVEAT)


def _kb_search_payload(results: list[Any]) -> dict[str, Any]:
    return {"results": results, "total": len(results)}


def _rag_query_payload(results: list[Any]) -> dict[str, Any]:
    return {"answer": "…", "citations": results, "context_used": len(results)}


def _ask_mentor_payload(results: list[Any]) -> dict[str, Any]:
    return {
        "answer": "…",
        "sources": results,
        "conversation_id": "c-1",
        "suggested_followups": [],
    }


@pytest.mark.asyncio
async def test_kb_search_caveats_live_write_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await _call_tool(
        "developer",
        "roboco_kb_search",
        {"query": "widgets API color field"},
        {
            "results": [_LIVE_WRITE_ITEM],
            "total": 1,
        },
        monkeypatch,
    )
    assert result["results"][0]["content"].endswith(_LIVE_WRITE_CAVEAT)


@pytest.mark.asyncio
async def test_kb_search_caveat_gates_on_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _assert_caveat_gates_on_provenance(
        "roboco_kb_search",
        {"query": "widgets API color field"},
        _kb_search_payload,
        "results",
        monkeypatch,
    )


@pytest.mark.asyncio
async def test_rag_query_caveat_gates_on_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _assert_caveat_gates_on_provenance(
        "roboco_rag_query",
        {"query": "how does the widgets API work?"},
        _rag_query_payload,
        "citations",
        monkeypatch,
    )


@pytest.mark.asyncio
async def test_ask_mentor_caveat_gates_on_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _assert_caveat_gates_on_provenance(
        "roboco_ask_mentor",
        {"question": "how does the widgets API work?"},
        _ask_mentor_payload,
        "sources",
        monkeypatch,
    )
