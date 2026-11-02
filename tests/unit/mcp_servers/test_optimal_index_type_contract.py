"""Every index name an agent-facing KB tool advertises is one the server serves.

2026-10-28: agents followed ``roboco_kb_search``'s own docstring, passed
``index_types=["conversations"]`` and got a 400 ("'conversations' is not a
valid IndexType") three times: the docstring still listed an index retired
with the channels/messages subsystem (#306). ``SEARCHABLE_INDEX_TYPES`` is the
one list; the plugin registry, the tool docs and the clear-index check must
all agree with it.
"""

from __future__ import annotations

import re
from http import HTTPStatus

import pytest
from fastapi import HTTPException
from roboco.api.utils.optimal import parse_index_types
from roboco.mcp.optimal_server import create_optimal_mcp_server
from roboco.models.optimal import SEARCHABLE_INDEX_TYPES
from roboco.services.optimal import PLUGIN_REGISTRY

_SEARCHABLE = {t.value for t in SEARCHABLE_INDEX_TYPES}
_INDEX_NAME = re.compile(r"[a-z_]+")


async def _tool_description(name: str, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("ROBOCO_ALLOW_FULL_TOOLSET", "1")
    monkeypatch.delenv("ROBOCO_AGENT_ROLE", raising=False)
    server = create_optimal_mcp_server("00000000-0000-0000-0000-000000000042")
    tools = {t.name: t for t in await server.list_tools()}
    return tools[name].description or ""


def _advertised_after(label: str, description: str) -> set[str]:
    """The index names listed right after ``label`` in a tool description."""
    start = description.index(label) + len(label)
    end = description.find("\n\n", start)
    listed = description[start : end if end != -1 else None]
    return set(_INDEX_NAME.findall(listed.replace("Index types to search", "")))


def test_plugin_registry_serves_exactly_the_searchable_indexes() -> None:
    assert set(PLUGIN_REGISTRY) == set(SEARCHABLE_INDEX_TYPES)


@pytest.mark.asyncio
async def test_kb_search_advertises_only_searchable_indexes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    description = await _tool_description("roboco_kb_search", monkeypatch)
    advertised = _advertised_after("index_types:", description)
    assert "conversations" not in advertised
    assert advertised == _SEARCHABLE


@pytest.mark.asyncio
async def test_clear_index_advertises_only_searchable_indexes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    description = await _tool_description("roboco_clear_index", monkeypatch)
    advertised = _advertised_after("index_type: One of:", description)
    assert advertised == _SEARCHABLE


def test_unknown_index_type_error_names_the_searchable_indexes() -> None:
    with pytest.raises(HTTPException) as caught:
        parse_index_types(["conversations"])
    assert caught.value.status_code == HTTPStatus.BAD_REQUEST
    for name in _SEARCHABLE:
        assert name in str(caught.value.detail)
