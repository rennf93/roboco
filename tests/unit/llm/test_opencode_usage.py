"""Tests for opencode usage capture (reading the opencode SQLite session table).

The fixture DB mirrors the real opencode v1.x ``session`` table columns observed
from a local run (cost + tokens_input/output/reasoning/cache_read/cache_write).
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from roboco.llm.providers.opencode_usage import (
    cost_for_session,
    read_session_usage,
)

if TYPE_CHECKING:
    from pathlib import Path

_M = 1_000_000
_TOL = 1e-4
_ZERO_COST = 0.0

# Single-session fixture: input, output, reasoning, cache_read, cache_write.
_IN, _OUT, _REASON, _CREAD, _CWRITE = 100, 50, 10, 20, 5
# Second session for the summation test.
_S2_IN, _S2_OUT, _S2_CREAD = 200, 70, 10
# grok-build-0.1: 1M input ($1.00) + 1M output ($2.00) = $3.00.
_GROK_COST_1M_1M = 3.00

# A REAL grok-build-0.1 session row observed from a live opencode run. Our
# pricing must reproduce opencode's own stored `cost` (= xAI authoritative).
_REAL_IN, _REAL_OUT, _REAL_REASON, _REAL_CREAD = 6120, 1, 226, 1856
_REAL_COST = 0.0069452


def _make_db(
    path: Path, rows: list[tuple[str, int, int, int, int, int, float]]
) -> None:
    con = sqlite3.connect(path)
    con.execute(
        """
        CREATE TABLE session (
            id text PRIMARY KEY,
            tokens_input integer DEFAULT 0 NOT NULL,
            tokens_output integer DEFAULT 0 NOT NULL,
            tokens_reasoning integer DEFAULT 0 NOT NULL,
            tokens_cache_read integer DEFAULT 0 NOT NULL,
            tokens_cache_write integer DEFAULT 0 NOT NULL,
            cost real DEFAULT 0 NOT NULL
        )
        """
    )
    con.executemany(
        "INSERT INTO session "
        "(id, tokens_input, tokens_output, tokens_reasoning, "
        "tokens_cache_read, tokens_cache_write, cost) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    con.commit()
    con.close()


def test_read_missing_db_returns_none(tmp_path: Path) -> None:
    assert read_session_usage(tmp_path / "nope.db") is None


def test_read_single_session(tmp_path: Path) -> None:
    db = tmp_path / "opencode.db"
    # (id, input, output, reasoning, cache_read, cache_write, cost)
    _make_db(db, [("s1", _IN, _OUT, _REASON, _CREAD, _CWRITE, 0.0007)])
    usage = read_session_usage(db, session_id="s1")
    assert usage is not None
    assert usage.tokens_input == _IN
    assert usage.tokens_output == _OUT
    assert usage.tokens_cache_read == _CREAD
    assert usage.tokens_cache_write == _CWRITE
    assert usage.tokens_reasoning == _REASON


def test_read_sums_all_sessions_when_no_id(tmp_path: Path) -> None:
    db = tmp_path / "opencode.db"
    _make_db(
        db,
        [
            ("s1", _IN, _OUT, 0, 0, 0, 0.0),
            ("s2", _S2_IN, _S2_OUT, 0, _S2_CREAD, 0, 0.0),
        ],
    )
    usage = read_session_usage(db)
    assert usage is not None
    assert usage.tokens_input == _IN + _S2_IN
    assert usage.tokens_output == _OUT + _S2_OUT
    assert usage.tokens_cache_read == _S2_CREAD


def test_read_empty_table_returns_none(tmp_path: Path) -> None:
    db = tmp_path / "opencode.db"
    _make_db(db, [])
    assert read_session_usage(db) is None


def test_cost_for_session_uses_roboco_pricing(tmp_path: Path) -> None:
    db = tmp_path / "opencode.db"
    # 1M input + 1M output for grok-build-0.1 → our $3.00, not opencode's 99.0.
    _make_db(db, [("s1", _M, _M, 0, 0, 0, 99.0)])
    usage, cost = cost_for_session("grok-build-0.1", db, session_id="s1")
    assert usage is not None
    assert abs(cost - _GROK_COST_1M_1M) < _TOL


def test_cost_for_session_missing_db(tmp_path: Path) -> None:
    usage, cost = cost_for_session("grok-build-0.1", tmp_path / "nope.db")
    assert usage is None
    assert cost == _ZERO_COST


def test_cost_reproduces_opencode_authoritative_cost(tmp_path: Path) -> None:
    """Real observed row: our pricing must match opencode's stored USD cost.

    Proves the column semantics (non-cached input disjoint from cache_read;
    reasoning separate, billed at output rate).
    """
    db = tmp_path / "opencode.db"
    # (id, input, output, reasoning, cache_read, cache_write, cost)
    _make_db(
        db,
        [("real", _REAL_IN, _REAL_OUT, _REAL_REASON, _REAL_CREAD, 0, _REAL_COST)],
    )
    usage, cost = cost_for_session("grok-build-0.1", db, session_id="real")
    assert usage is not None
    assert abs(cost - _REAL_COST) < _TOL
    assert abs(cost - usage.opencode_cost) < _TOL
