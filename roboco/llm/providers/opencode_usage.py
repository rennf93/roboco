"""Read token usage from an opencode SQLite store — Grok agent cost capture.

opencode (v1.x) persists per-session usage in a SQLite DB at
``~/.local/share/opencode/opencode.db`` (confirmed by inspecting a local run:
the ``session`` table carries ``cost`` and ``tokens_input`` / ``tokens_output``
/ ``tokens_reasoning`` / ``tokens_cache_read`` / ``tokens_cache_write``).

A Grok agent runs opencode, so its usage lands there rather than in a Claude
Code transcript. The orchestrator reads this at agent finalize and feeds the
token counts to :func:`roboco.billing.pricing.calculate_cost` — keeping our
pricing authoritative — while opencode's own ``cost`` column is kept for
reference. A per-agent container has a single opencode store, so summing all
session rows is correct without needing to map an opencode session id.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from roboco.billing.pricing import calculate_cost

# Default location inside the agent container (HOME=/home/agent).
DEFAULT_DB_PATH = "/home/agent/.local/share/opencode/opencode.db"

# Column order here MUST match the unpacking in read_session_usage below.
_SELECT_ALL = (
    "SELECT tokens_input, tokens_output, tokens_cache_read, "
    "tokens_cache_write, tokens_reasoning, cost FROM session"
)
_SELECT_ONE = _SELECT_ALL + " WHERE id = ?"


@dataclass(frozen=True)
class OpencodeUsage:
    """Aggregated token usage read from an opencode store."""

    tokens_input: int
    tokens_output: int
    tokens_cache_read: int
    tokens_cache_write: int
    tokens_reasoning: int
    opencode_cost: float  # opencode's own computed cost (reference only)


def read_session_usage(
    db_path: str | Path = DEFAULT_DB_PATH,
    session_id: str | None = None,
) -> OpencodeUsage | None:
    """Read aggregated usage from an opencode SQLite store.

    Reads the ``session`` table — a specific row when ``session_id`` is given,
    otherwise the sum across all sessions (one store per agent container).
    Returns ``None`` if the DB is missing, the table absent, or there are no
    rows — never raises, so callers don't need to guard finalize on it.
    """
    path = Path(db_path)
    if not path.exists():
        return None

    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            if session_id is not None:
                cur = con.execute(_SELECT_ONE, (session_id,))
            else:
                cur = con.execute(_SELECT_ALL)
            rows = cur.fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return None

    if not rows:
        return None

    totals = [0, 0, 0, 0, 0]
    cost = 0.0
    for row in rows:
        for i in range(5):
            totals[i] += int(row[i] or 0)
        cost += float(row[5] or 0.0)

    return OpencodeUsage(
        tokens_input=totals[0],
        tokens_output=totals[1],
        tokens_cache_read=totals[2],
        tokens_cache_write=totals[3],
        tokens_reasoning=totals[4],
        opencode_cost=cost,
    )


def cost_for_session(
    model: str,
    db_path: str | Path = DEFAULT_DB_PATH,
    session_id: str | None = None,
) -> tuple[OpencodeUsage | None, float]:
    """Return (usage, roboco_cost_usd) for a Grok agent's opencode session.

    ``roboco_cost_usd`` is computed from our own pricing table so cost is
    consistent with the Claude path. Returns ``(None, 0.0)`` when no usage is
    recorded yet.
    """
    usage = read_session_usage(db_path, session_id)
    if usage is None:
        return None, 0.0
    # opencode stores tokens_input as non-cached input (disjoint from
    # tokens_cache_read) and tokens_output EXCLUDING reasoning, with reasoning
    # in its own column. Reasoning bills at the output rate, so fold it into
    # output. Verified against a live run: this reproduces opencode's own `cost`
    # column (= xAI's authoritative cost) to the cent.
    cost = calculate_cost(
        model,
        tokens_input=usage.tokens_input,
        tokens_output=usage.tokens_output + usage.tokens_reasoning,
        tokens_cache_read=usage.tokens_cache_read,
        tokens_cache_write=usage.tokens_cache_write,
    )
    return usage, cost
