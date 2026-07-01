"""sum_transcript_usage — token + turn counts from a Claude Code JSONL transcript.

The 5th return value is the LLM turn count: the number of UNIQUE assistant
``message.id``s (Claude Code logs one line per content block, all sharing the
message id, so naive line-counting would inflate both tokens and turns).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from roboco.agent_sdk.transcript_usage import sum_transcript_usage

if TYPE_CHECKING:
    from pathlib import Path

_EXPECTED_TUPLE_LEN = 5


def _line(msg_id: str | None, **usage: int) -> str:
    msg: dict[str, object] = {"usage": usage}
    if msg_id is not None:
        msg["id"] = msg_id
    return json.dumps({"message": msg})


def _write(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_returns_five_tuple(tmp_path: Path) -> None:
    f = tmp_path / "t.jsonl"
    _write(f, [_line("m1", input_tokens=10, output_tokens=5)])
    result = sum_transcript_usage(f)
    assert len(result) == _EXPECTED_TUPLE_LEN


def test_turns_counts_unique_message_ids(tmp_path: Path) -> None:
    f = tmp_path / "t.jsonl"
    _write(
        f,
        [
            _line("m1", input_tokens=10, output_tokens=5),
            _line("m2", input_tokens=20, output_tokens=7),
            _line("m3", input_tokens=1, output_tokens=1),
        ],
    )
    _in, _out, _cr, _cw, turns = sum_transcript_usage(f)
    expected_turns = 3
    assert turns == expected_turns


def test_repeated_message_id_counts_one_turn_and_one_usage(tmp_path: Path) -> None:
    # Claude Code emits one line per content block of the SAME assistant message,
    # each repeating the usage — must count once for tokens AND turns.
    f = tmp_path / "t.jsonl"
    _write(
        f,
        [
            _line("m1", input_tokens=10, output_tokens=5),
            _line("m1", input_tokens=10, output_tokens=5),
            _line("m1", input_tokens=10, output_tokens=5),
        ],
    )
    tin, tout, _cr, _cw, turns = sum_transcript_usage(f)
    assert (tin, tout, turns) == (10, 5, 1)


def test_malformed_lines_skipped_without_losing_turn_count(tmp_path: Path) -> None:
    f = tmp_path / "t.jsonl"
    _write(
        f,
        [
            _line("m1", input_tokens=10, output_tokens=5),
            "not json at all {{{",
            "",
            _line("m2", input_tokens=2, output_tokens=2),
        ],
    )
    tin, _out, _cr, _cw, turns = sum_transcript_usage(f)
    assert (tin, turns) == (12, 2)


def test_usage_line_without_id_sums_tokens_but_not_a_turn(tmp_path: Path) -> None:
    f = tmp_path / "t.jsonl"
    _write(
        f,
        [
            _line(None, input_tokens=4, output_tokens=1),
            _line("m1", input_tokens=6, output_tokens=1),
        ],
    )
    tin, _out, _cr, _cw, turns = sum_transcript_usage(f)
    assert (tin, turns) == (10, 1)
