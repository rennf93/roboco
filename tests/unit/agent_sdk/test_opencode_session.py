"""normalize_opencode_message maps opencode message parts to panel chunks.

The OpencodeServeSession transport (subprocess + HTTP) needs a live opencode,
like the Claude SdkIntakeSession; the deterministic part→chunk mapping and the
session-id extraction are covered here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from roboco.agent_sdk.opencode_session import (
    _extract_session_id,
    normalize_opencode_message,
)

if TYPE_CHECKING:
    from roboco.agent_sdk.intake_driver import StreamChunk


def _kinds(chunks: list[StreamChunk]) -> list[str]:
    return [c.kind for c in chunks]


def test_text_part_emits_text_then_turn_end() -> None:
    chunks = normalize_opencode_message([{"type": "text", "text": "Hi there"}])
    assert _kinds(chunks) == ["text", "turn_end"]
    assert chunks[0].text == "Hi there"


def test_reasoning_part_maps_to_thinking() -> None:
    chunks = normalize_opencode_message([{"type": "reasoning", "text": "hmm"}])
    assert chunks[0].kind == "thinking"
    assert chunks[0].text == "hmm"


def test_tool_part_maps_to_tool_use() -> None:
    chunks = normalize_opencode_message(
        [{"type": "tool", "tool": "read", "input": {"path": "x"}}]
    )
    tool = next(c for c in chunks if c.kind == "tool_use")
    assert tool.tool == "read"
    assert tool.data == {"input": {"path": "x"}}


def test_fenced_draft_in_text_becomes_draft_chunk() -> None:
    fenced = '```roboco-draft\n{"title": "Add login"}\n```'
    chunks = normalize_opencode_message([{"type": "text", "text": fenced}])
    draft = next(c for c in chunks if c.kind == "draft")
    assert draft.data["title"] == "Add login"


def test_unknown_part_skipped_but_turn_still_ends() -> None:
    chunks = normalize_opencode_message([{"type": "mystery", "x": 1}])
    assert _kinds(chunks) == ["turn_end"]


def test_empty_message_yields_only_turn_end() -> None:
    assert _kinds(normalize_opencode_message([])) == ["turn_end"]


def test_extract_session_id_is_tolerant() -> None:
    assert _extract_session_id({"id": "s1"}) == "s1"
    assert _extract_session_id({"sessionID": "s2"}) == "s2"
    assert _extract_session_id({"info": {"id": "s3"}}) == "s3"
    assert _extract_session_id({}) is None
    assert _extract_session_id("nope") is None
