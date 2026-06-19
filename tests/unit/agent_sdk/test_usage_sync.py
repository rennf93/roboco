"""Token-usage capture — /usage/sync parses the transcript and sets totals.

The agent SDK exposes /usage/report (additive, used by Grok via usage-report-hook)
and /usage/status (read). Claude Code never emits deltas in hooks, so the
usage-report-hook falls back to transcript_path + /usage/sync which parses
per-message ``usage`` blocks and *sets* totals absolutely (idempotent). These
tests pin the sync contract. Grok path exercises /report directly.

Expected totals are derived from the input rows (no magic literals), so the
assertions track whatever the fixtures declare.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
import roboco.agent_sdk.server as srv
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path

_OK = 200

# Each row is (input, output, cache_read, cache_write).
_UsageRow = tuple[int, int, int, int]


@pytest.fixture(autouse=True)
def _reset_state() -> Iterator[None]:
    srv._state.reset()
    yield
    srv._state.reset()


@pytest.fixture
def client() -> TestClient:
    return TestClient(srv.app)


def _assistant_line(row: _UsageRow) -> str:
    inp, out, cread, cwrite = row
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "usage": {
                    "input_tokens": inp,
                    "output_tokens": out,
                    "cache_read_input_tokens": cread,
                    "cache_creation_input_tokens": cwrite,
                },
            },
        }
    )


def _write(path: Path, *lines: str) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _expected(rows: Sequence[_UsageRow]) -> dict[str, int]:
    return {
        "tokens_input": sum(r[0] for r in rows),
        "tokens_output": sum(r[1] for r in rows),
        "tokens_cache_read": sum(r[2] for r in rows),
        "tokens_cache_write": sum(r[3] for r in rows),
    }


def test_sums_usage_across_assistant_messages(
    client: TestClient, tmp_path: Path
) -> None:
    rows: list[_UsageRow] = [(100, 20, 5, 3), (50, 10, 2, 1)]
    transcript = tmp_path / "session.jsonl"
    _write(transcript, *(_assistant_line(r) for r in rows))
    resp = client.post("/usage/sync", json={"transcript_path": str(transcript)})
    assert resp.status_code == _OK
    body = resp.json()
    for key, value in _expected(rows).items():
        assert body[key] == value


def test_status_reflects_synced_totals(client: TestClient, tmp_path: Path) -> None:
    rows: list[_UsageRow] = [(200, 40, 0, 0)]
    transcript = tmp_path / "session.jsonl"
    _write(transcript, *(_assistant_line(r) for r in rows))
    client.post("/usage/sync", json={"transcript_path": str(transcript)})
    status = client.get("/usage/status").json()
    for key, value in _expected(rows).items():
        assert status[key] == value


def test_resync_is_idempotent_not_additive(client: TestClient, tmp_path: Path) -> None:
    """The set is absolute — syncing the same transcript twice must not double."""
    rows: list[_UsageRow] = [(100, 20, 0, 0)]
    transcript = tmp_path / "session.jsonl"
    _write(transcript, *(_assistant_line(r) for r in rows))
    client.post("/usage/sync", json={"transcript_path": str(transcript)})
    client.post("/usage/sync", json={"transcript_path": str(transcript)})
    status = client.get("/usage/status").json()
    for key, value in _expected(rows).items():
        assert status[key] == value


def test_resync_after_growth_overwrites_with_new_total(
    client: TestClient, tmp_path: Path
) -> None:
    first: list[_UsageRow] = [(100, 20, 0, 0)]
    grown: list[_UsageRow] = [(100, 20, 0, 0), (80, 15, 0, 0)]
    transcript = tmp_path / "session.jsonl"
    _write(transcript, *(_assistant_line(r) for r in first))
    client.post("/usage/sync", json={"transcript_path": str(transcript)})
    # The transcript grows as the turn continues.
    _write(transcript, *(_assistant_line(r) for r in grown))
    client.post("/usage/sync", json={"transcript_path": str(transcript)})
    status = client.get("/usage/status").json()
    for key, value in _expected(grown).items():
        assert status[key] == value


def test_missing_transcript_returns_zero_without_error(
    client: TestClient, tmp_path: Path
) -> None:
    resp = client.post(
        "/usage/sync", json={"transcript_path": str(tmp_path / "nope.jsonl")}
    )
    assert resp.status_code == _OK
    assert resp.json() == _expected([])


def test_malformed_lines_are_skipped(client: TestClient, tmp_path: Path) -> None:
    rows: list[_UsageRow] = [(100, 20, 0, 0), (50, 10, 0, 0)]
    transcript = tmp_path / "session.jsonl"
    _write(
        transcript,
        "not json at all",
        _assistant_line(rows[0]),
        json.dumps({"type": "user", "message": {"role": "user"}}),  # no usage
        "{ broken",
        _assistant_line(rows[1]),
    )
    body = client.post("/usage/sync", json={"transcript_path": str(transcript)}).json()
    exp = _expected(rows)
    assert body["tokens_input"] == exp["tokens_input"]
    assert body["tokens_output"] == exp["tokens_output"]


def test_parser_handles_entries_without_message(tmp_path: Path) -> None:
    rows: list[_UsageRow] = [(10, 5, 0, 0)]
    transcript = tmp_path / "session.jsonl"
    _write(
        transcript,
        json.dumps({"type": "system", "subtype": "init"}),
        _assistant_line(rows[0]),
    )
    tin, tout, cread, cwrite = srv._sum_transcript_usage(transcript)
    exp = _expected(rows)
    assert (tin, tout, cread, cwrite) == (
        exp["tokens_input"],
        exp["tokens_output"],
        exp["tokens_cache_read"],
        exp["tokens_cache_write"],
    )


def _assistant_line_with_id(row: _UsageRow, message_id: str) -> str:
    """An assistant line carrying a message id (for de-duplication tests)."""
    inp, out, cread, cwrite = row
    return json.dumps(
        {
            "type": "assistant",
            "message": {
                "id": message_id,
                "role": "assistant",
                "usage": {
                    "input_tokens": inp,
                    "output_tokens": out,
                    "cache_read_input_tokens": cread,
                    "cache_creation_input_tokens": cwrite,
                },
            },
        }
    )


def test_parser_dedupes_repeated_message_id(tmp_path: Path) -> None:
    """One message logged across several lines (same id) is counted once.

    Claude Code emits one transcript line per content block (thinking, text,
    tool_use), each repeating the message's ``usage``. Summing every line would
    roughly double the totals, so the parser must de-duplicate by message id.
    """
    msg = (100, 20, 5, 3)
    other = (7, 2, 1, 0)
    transcript = tmp_path / "session.jsonl"
    _write(
        transcript,
        _assistant_line_with_id(msg, "msg_aaa"),  # thinking block
        _assistant_line_with_id(msg, "msg_aaa"),  # text block (same id)
        _assistant_line_with_id(msg, "msg_aaa"),  # tool_use block (same id)
        _assistant_line_with_id(other, "msg_bbb"),
    )
    tin, tout, cread, cwrite = srv._sum_transcript_usage(transcript)
    # Counted once per id: msg + other, NOT msg * 3 + other.
    exp = _expected([msg, other])
    assert (tin, tout, cread, cwrite) == (
        exp["tokens_input"],
        exp["tokens_output"],
        exp["tokens_cache_read"],
        exp["tokens_cache_write"],
    )


# =============================================================================
# Grok /usage/report additive path (direct deltas from usage-report-hook)
# =============================================================================


def test_report_additive_deltas(client: TestClient) -> None:
    """/usage/report sums deltas across calls (Grok hook path)."""
    r1 = client.post(
        "/usage/report",
        json={
            "tokens_input": 120,
            "tokens_output": 30,
            "tokens_cache_read": 10,
            "tokens_cache_write": 2,
        },
    )
    assert r1.status_code == _OK
    assert r1.json() == {
        "tokens_input": 120,
        "tokens_output": 30,
        "tokens_cache_read": 10,
        "tokens_cache_write": 2,
    }

    r2 = client.post(
        "/usage/report",
        json={"tokens_input": 40, "tokens_output": 15, "tokens_cache_read": 0, "tokens_cache_write": 0},
    )
    assert r2.status_code == _OK
    assert r2.json() == {
        "tokens_input": 160,
        "tokens_output": 45,
        "tokens_cache_read": 10,
        "tokens_cache_write": 2,
    }

    status = client.get("/usage/status").json()
    assert status["tokens_input"] == 160
    assert status["tokens_output"] == 45


def test_report_defaults_to_zero_and_accumulates(client: TestClient) -> None:
    """Missing fields default 0; report remains additive."""
    client.post("/usage/report", json={"tokens_input": 50, "tokens_output": 10})
    client.post("/usage/report", json={"tokens_output": 5, "tokens_cache_read": 3})
    status = client.get("/usage/status").json()
    assert status["tokens_input"] == 50
    assert status["tokens_output"] == 15
    assert status["tokens_cache_read"] == 3
    assert status["tokens_cache_write"] == 0
