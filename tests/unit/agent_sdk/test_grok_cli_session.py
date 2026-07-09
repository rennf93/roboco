"""grok_cli_session — the pure streaming-json → StreamChunk mapper.

The subprocess runner (``GrokCliSession.send``) needs the live grok binary, so it
is not gate-covered; the turn-mapping logic lives in the pure ``_StreamAssembler``
and is fully exercised here by feeding it parsed events. The synchronous
``__init__`` (role resolution, per-role flags, timeout) IS pure and tested.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from roboco.agent_sdk.grok_cli_session import (
    GrokCliSession,
    _classify_failure,
    _parse_event,
    _StreamAssembler,
    _turn_timeout_seconds,
)
from roboco.llm.providers.grok_cli_config import grok_cli_args_for_role

if TYPE_CHECKING:
    import pytest


def _kinds(chunks: list) -> list[str]:
    return [c.kind for c in chunks]


def test_thought_deltas_coalesce_into_one_thinking_block() -> None:
    a = _StreamAssembler()
    out: list = []
    for piece in ("Let", " me", " think"):
        out += a.feed({"type": "thought", "data": piece})
    # Nothing emitted until the answer starts (reasoning shown as one block).
    assert out == []
    out += a.feed({"type": "text", "data": "Hello"})
    assert _kinds(out) == ["thinking", "text"]
    assert out[0].text == "Let me think"
    assert out[1].text == "Hello"


def test_text_deltas_stream_live() -> None:
    a = _StreamAssembler()
    out: list = []
    for piece in ("a", "b", "c"):
        out += a.feed({"type": "text", "data": piece})
    assert _kinds(out) == ["text", "text", "text"]
    assert "".join(c.text for c in out) == "abc"


def test_end_captures_session_id_and_emits_turn_end() -> None:
    a = _StreamAssembler()
    a.feed({"type": "text", "data": "hi"})
    out = a.feed({"type": "end", "sessionId": "sid-9", "stopReason": "EndTurn"})
    assert _kinds(out) == ["turn_end"]
    assert a.session_id == "sid-9"
    assert a.saw_end is True
    assert out[-1].data["session_id"] == "sid-9"


def test_end_flushes_pending_thinking_before_turn_end() -> None:
    a = _StreamAssembler()
    a.feed({"type": "thought", "data": "reasoning only"})
    out = a.feed({"type": "end", "sessionId": "s", "stopReason": "EndTurn"})
    assert _kinds(out) == ["thinking", "turn_end"]


def test_fenced_draft_is_surfaced_as_a_draft_chunk() -> None:
    a = _StreamAssembler()
    draft = {"title": "Build X", "objective": "do it"}
    a.feed({"type": "text", "data": "Here:\n```roboco-draft\n"})
    a.feed({"type": "text", "data": json.dumps(draft)})
    a.feed({"type": "text", "data": "\n```\n"})
    out = a.feed({"type": "end", "sessionId": "s", "stopReason": "EndTurn"})
    assert "draft" in _kinds(out)
    draft_chunk = next(c for c in out if c.kind == "draft")
    assert draft_chunk.data["title"] == "Build X"


def test_unknown_event_types_are_ignored() -> None:
    a = _StreamAssembler()
    assert a.feed({"type": "tool", "name": "whatever"}) == []
    assert a.feed({"type": "", "data": "x"}) == []


def test_parse_event_is_tolerant() -> None:
    assert _parse_event('{"type":"text","data":"x"}') == {"type": "text", "data": "x"}
    assert _parse_event("not json") is None
    assert _parse_event("[1,2,3]") is None  # not a dict


def test_classify_failure_detects_rate_limit() -> None:
    msg = _classify_failure(1, "xAI error: 429 too many requests")
    assert "rate-limited" in msg.lower()


def test_classify_failure_generic_uses_last_stderr_line() -> None:
    msg = _classify_failure(2, "warming up\nboom: the model exploded")
    assert "boom: the model exploded" in msg
    # With no stderr, the exit code is surfaced.
    assert "exit code 2" in _classify_failure(2, "")


def test_session_resolves_role_from_env_when_id_is_a_uuid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The secretary's ROBOCO_AGENT_ID is a UUID; get_agent_role returns the
    # "unknown" sentinel for it, so the role must fall back to ROBOCO_AGENT_ROLE
    # (not silently use "unknown").
    monkeypatch.setenv("ROBOCO_AGENT_ROLE", "secretary")
    monkeypatch.delenv("ROBOCO_GROK_REASONING_EFFORT", raising=False)
    session = GrokCliSession(cwd="/app", agent_id="0192-uuid-not-a-slug")
    assert session._role_args == grok_cli_args_for_role("secretary")


def test_session_uses_slug_role_when_id_maps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ROBOCO_AGENT_ROLE", raising=False)
    monkeypatch.delenv("ROBOCO_GROK_REASONING_EFFORT", raising=False)
    # intake-1 maps to the prompter role; the slug-derived role args must
    # carry the fleet-wide subagent ban (Agent disallowed for every role).
    session = GrokCliSession(cwd="/ws", agent_id="intake-1")
    dis = session._role_args[session._role_args.index("--disallowed-tools") + 1]
    assert "Agent" in dis


def test_turn_timeout_seconds_env_and_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ROBOCO_GROK_TURN_TIMEOUT_SECONDS", raising=False)
    assert _turn_timeout_seconds() == 600.0  # noqa: PLR2004
    monkeypatch.setenv("ROBOCO_GROK_TURN_TIMEOUT_SECONDS", "120")
    assert _turn_timeout_seconds() == 120.0  # noqa: PLR2004
    # Garbage / non-positive falls back to the default.
    monkeypatch.setenv("ROBOCO_GROK_TURN_TIMEOUT_SECONDS", "nope")
    assert _turn_timeout_seconds() == 600.0  # noqa: PLR2004
    monkeypatch.setenv("ROBOCO_GROK_TURN_TIMEOUT_SECONDS", "0")
    assert _turn_timeout_seconds() == 600.0  # noqa: PLR2004
