"""Spawner attribution: every ``agent.spawned`` audit row names its dispatcher.

During the 2026-07-02 live run a rogue spawner could not be identified from the
audit log — ``agent.spawned`` rows carry the container/model but not WHICH
dispatch loop launched them. ``spawn_agent`` now takes ``spawned_by`` and
stamps it into the ``agent.spawned`` / ``agent.spawn_failed`` details, and an
AST sweep holds every call site to passing it.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from roboco.models.runtime import AgentInstance
from roboco.runtime.orchestrator import AgentConfig, AgentOrchestrator, AgentState

REPO_ROOT = Path(__file__).resolve().parents[3]


def _make_orchestrator(captured: list[dict[str, Any]]) -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._instances = {}
    orch._lock = asyncio.Lock()
    orch._bg_tasks = set()
    orch._running = True
    orch._fire_audit = lambda **kw: captured.append(kw)  # type: ignore[method-assign]
    orch._record_spawn_session = AsyncMock(return_value=None)
    return orch


def _config_and_instance() -> tuple[AgentConfig, AgentInstance]:
    config = AgentConfig(
        agent_id="be-dev-1",
        blueprint_path=Path(),
        model="opus",
        provider_type="anthropic",
    )
    instance = AgentInstance(
        agent_id="be-dev-1", state=AgentState.STARTING, config=config
    )
    return config, instance


@pytest.mark.asyncio
async def test_launch_spawn_audit_carries_spawned_by() -> None:
    """``agent.spawned`` details must name the dispatcher that launched it."""
    captured: list[dict[str, Any]] = []
    orch = _make_orchestrator(captured)
    orch._spawn_container = AsyncMock(return_value="c0ffee" * 11)
    config, instance = _config_and_instance()

    await orch._launch_spawn(
        "task-1", config, instance, None, None, spawned_by="_dispatch_qa_work"
    )

    spawned = [c for c in captured if c["event_type"] == "agent.spawned"]
    assert len(spawned) == 1
    assert spawned[0]["details"]["spawned_by"] == "_dispatch_qa_work"


@pytest.mark.asyncio
async def test_spawn_failed_audit_carries_spawned_by() -> None:
    """A failed launch must attribute the spawner too — a rogue dispatcher
    that keeps crashing containers is exactly the live-debug case."""
    captured: list[dict[str, Any]] = []
    orch = _make_orchestrator(captured)
    orch._spawn_container = AsyncMock(side_effect=RuntimeError("boom"))
    config, instance = _config_and_instance()

    with pytest.raises(RuntimeError):
        await orch._launch_spawn(
            "task-1", config, instance, None, None, spawned_by="_spawn_pending_dev"
        )

    failed = [c for c in captured if c["event_type"] == "agent.spawn_failed"]
    assert len(failed) == 1
    assert failed[0]["details"]["spawned_by"] == "_spawn_pending_dev"


@pytest.mark.asyncio
async def test_launch_spawn_without_attribution_stamps_unspecified() -> None:
    """The field is always present so audit queries never KeyError."""
    captured: list[dict[str, Any]] = []
    orch = _make_orchestrator(captured)
    orch._spawn_container = AsyncMock(return_value="c0ffee" * 11)
    config, instance = _config_and_instance()

    await orch._launch_spawn("task-1", config, instance, None, None)

    spawned = [c for c in captured if c["event_type"] == "agent.spawned"]
    assert spawned[0]["details"]["spawned_by"] == "unspecified"


def _spawn_agent_calls_missing_spawned_by(path: Path) -> list[str]:
    """Return ``file:line`` for spawn_agent() calls without a spawned_by kwarg."""
    tree = ast.parse(path.read_text())
    missing: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "spawn_agent"):
            continue
        if not any(kw.arg == "spawned_by" for kw in node.keywords):
            missing.append(f"{path.name}:{node.lineno}")
    return missing


def test_every_spawn_agent_call_site_passes_spawned_by() -> None:
    """Sweep-guard over the whole package: a dispatcher added without
    attribution fails here, not in a 3am live-debug session."""
    missing: list[str] = []
    for path in sorted((REPO_ROOT / "roboco").rglob("*.py")):
        missing.extend(_spawn_agent_calls_missing_spawned_by(path))
    assert not missing, f"spawn_agent() calls missing spawned_by=: {missing}"
