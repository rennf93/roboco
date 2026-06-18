"""GROK agents capture token usage/cost from their opencode SQLite store.

A Grok agent runs opencode — no SDK /usage/status server and no Claude
transcript — so finalize must read opencode.db (mounted into the orchestrator)
instead. Reasoning folds into output (it bills at the output rate).
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest
from roboco.models.runtime import AgentInstance
from roboco.runtime.orchestrator import AgentOrchestrator

if TYPE_CHECKING:
    from pathlib import Path


def _make_db(path: Path, cols: dict[str, float]) -> None:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE session (id TEXT, tokens_input INT, tokens_output INT, "
        "tokens_cache_read INT, tokens_cache_write INT, tokens_reasoning INT, "
        "cost REAL)"
    )
    con.execute(
        "INSERT INTO session (id, tokens_input, tokens_output, tokens_cache_read, "
        "tokens_cache_write, tokens_reasoning, cost) VALUES (?,?,?,?,?,?,?)",
        (
            "s1",
            cols["tokens_input"],
            cols["tokens_output"],
            cols["tokens_cache_read"],
            cols["tokens_cache_write"],
            cols["tokens_reasoning"],
            cols["cost"],
        ),
    )
    con.commit()
    con.close()


def test_grok_usage_folds_reasoning_into_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "opencode.db"
    _make_db(
        db,
        {
            "tokens_input": 100,
            "tokens_output": 50,
            "tokens_reasoning": 30,
            "tokens_cache_read": 10,
            "tokens_cache_write": 5,
            "cost": 0.02,
        },
    )
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(orch, "_opencode_db_path", lambda _aid: str(db))

    # reasoning (30) folded into output (50) → 80; bills at the output rate.
    assert orch._grok_usage_from_opencode("be-dev-1") == (100, 80, 10, 5)


def test_grok_usage_zero_when_store_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(
        orch, "_opencode_db_path", lambda _aid: str(tmp_path / "absent.db")
    )
    assert orch._grok_usage_from_opencode("be-dev-1") == (0, 0, 0, 0)


@pytest.mark.asyncio
async def test_resolve_final_usage_routes_grok_to_opencode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "opencode.db"
    _make_db(
        db,
        {
            "tokens_input": 7,
            "tokens_output": 3,
            "tokens_reasoning": 2,
            "tokens_cache_read": 0,
            "tokens_cache_write": 0,
            "cost": 0.01,
        },
    )
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(orch, "_opencode_db_path", lambda _aid: str(db))
    cfg = type("C", (), {"provider_type": "grok"})()
    orch._instances = {"be-dev-1": AgentInstance(agent_id="be-dev-1", config=cfg)}

    # No SDK fetch / transcript read for GROK — usage comes from opencode.db.
    assert await orch._resolve_final_token_usage("be-dev-1") == (7, 5, 0, 0)
