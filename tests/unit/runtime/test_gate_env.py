"""Faithful-gate env injection.

An agent's gate (`make quality` / pytest) needs a reachable Postgres or the
conftest skips every DB-backed integration test and coverage collapses far
below the threshold — a hollow gate. `_append_gate_env` injects the test-DB
connection (from the orchestrator's own settings) so the agent runs the real
suite, gated on the same faithful-gate flag as interpreter matching.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from roboco.config import settings
from roboco.runtime.orchestrator import AgentOrchestrator

if TYPE_CHECKING:
    import pytest


def test_gate_env_injects_test_db_when_flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "toolchain_match_enabled", True)
    monkeypatch.setattr(settings, "database_host", "roboco-postgres")
    monkeypatch.setattr(settings, "database_port", 5432)
    monkeypatch.setattr(settings, "database_user", "roboco")
    monkeypatch.setattr(settings, "database_password", "s3cret")
    cmd: list[str] = []
    AgentOrchestrator._append_gate_env(cmd)
    assert "ROBOCO_TEST_DB_HOST=roboco-postgres" in cmd
    assert "ROBOCO_TEST_DB_PORT=5432" in cmd
    assert "ROBOCO_TEST_DB_USER=roboco" in cmd
    assert "ROBOCO_TEST_DB_PASSWORD=s3cret" in cmd
    assert "ROBOCO_TEST_DB_ADMIN_DB=postgres" in cmd


def test_gate_env_inert_when_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "toolchain_match_enabled", False)
    cmd: list[str] = []
    AgentOrchestrator._append_gate_env(cmd)
    assert cmd == []
