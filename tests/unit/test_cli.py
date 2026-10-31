"""``python -m roboco.cli``: each ``asyncio.run`` closes the database engines
it opened before its loop ends; nothing could close them afterwards."""

from __future__ import annotations

import sys

import pytest
from roboco import cli
from roboco.config import settings
from roboco.db import base as db_base


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    recorded: list[str] = []

    async def _close_db() -> None:
        recorded.append("close_db")

    monkeypatch.setattr(db_base, "close_db", _close_db)
    monkeypatch.setattr(settings, "uvicorn_loop", "asyncio")
    return recorded


def test_db_only_closes_the_engines_after_the_bootstrap(
    calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _bootstrap_database() -> None:
        calls.append("bootstrap_database")

    monkeypatch.setattr(sys, "argv", ["roboco", "--db-only"])
    monkeypatch.setattr(cli, "bootstrap_database", _bootstrap_database)

    cli.cli()

    assert calls == ["bootstrap_database", "close_db"]


def test_a_full_run_closes_the_engines_after_main_returns(
    calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--skip-orchestrator`` returns right after the database bootstrap,
    before any API lifespan could close the engine."""

    async def _main(
        skip_db: bool, skip_orchestrator: bool, spawn_agents: list[str] | None
    ) -> None:
        calls.append(f"main({skip_db=}, {skip_orchestrator=}, {spawn_agents=})")

    monkeypatch.setattr(sys, "argv", ["roboco", "--skip-orchestrator"])
    monkeypatch.setattr(cli, "main", _main)

    cli.cli()

    assert calls == [
        "main(skip_db=False, skip_orchestrator=True, spawn_agents=None)",
        "close_db",
    ]
