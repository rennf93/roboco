"""grok_cli_session — the pure streaming-json → StreamChunk mapper.

The subprocess runner (``GrokCliSession``) needs the live grok binary, so it is
not gate-covered; the turn-mapping logic lives in the pure ``_StreamAssembler``
and is fully exercised here by feeding it parsed events.
"""

from __future__ import annotations

import json

from roboco.agent_sdk.grok_cli_session import (
    _classify_failure,
    _parse_event,
    _StreamAssembler,
)


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
