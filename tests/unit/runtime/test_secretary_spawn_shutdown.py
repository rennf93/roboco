"""F071 — the Secretary non-blocking spawn (``start_secretary_session`` →
``_schedule_bg(_spawn_secretary_container_guarded)``) runs ``docker run`` and
only registers the instance in ``_instances`` at the END. If shutdown arrives
between ``docker run`` and the registration line, the container is started but
the orchestrator has no handle to it — ``stop()`` iterates only ``_instances``,
so the container is orphaned (leaked, must be cleaned up with ``docker rm``).
Worse, the F070 drain can let the spawn coroutine COMPLETE the registration
AFTER ``stop()`` already iterated ``_instances``, landing a live container into
a shutting-down registry that nothing tears down.

The fix: after ``docker run`` returns the container id, re-check ``self._running``
and, if the orchestrator began shutting down, remove the just-started container
and abort WITHOUT registering. The guarded wrapper closes the relay silently
(shutdown is not a user-facing failure).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch
from uuid import UUID

import pytest
from roboco.runtime.orchestrator import (
    SECRETARY_AGENT_ID,
    AgentOrchestrator,
)
from roboco.services import prompter_live


def _make_orchestrator() -> AgentOrchestrator:
    """AgentOrchestrator with constructor I/O skipped; a RUNNING minimal one."""
    with patch.object(AgentOrchestrator, "__init__", return_value=None):
        orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._instances = {}
    orch._bg_tasks = set()
    orch._running = True
    return orch


def _wire_secretary_spawn_mocks(
    monkeypatch: pytest.MonkeyPatch,
    orch: AgentOrchestrator,
    removed: list[str],
    *,
    flip_running_on_run: bool,
) -> None:
    """Patch every external boundary _spawn_secretary_container touches."""

    async def _noop(*_a: Any, **_k: Any) -> None:
        return None

    async def _route(_aid: str) -> Any:
        return SimpleNamespace(
            provider_type=SimpleNamespace(value="anthropic"),
            model_name="opus",
            base_url=None,
            auth_token=None,
        )

    async def _run(_cmd: list[str]) -> str:
        if flip_running_on_run:
            # Shutdown arrives AFTER docker run started the container but BEFORE
            # the registration line runs.
            orch._running = False
        return "containerid0123456789"

    async def _remove(name: str) -> None:
        removed.append(name)

    monkeypatch.setattr(
        orch,
        "_generate_composed_prompt",
        lambda *_a, **_k: Path("/tmp/secretary-prompt.md"),
    )
    monkeypatch.setattr(orch, "_resolve_agent_route", _route)
    monkeypatch.setattr(orch, "_ensure_agent_image", _noop)
    monkeypatch.setattr(orch, "_remove_container", _remove)
    monkeypatch.setattr(orch, "_run_container_cmd", _run)
    monkeypatch.setattr(
        orch,
        "_resolve_secretary_host_paths",
        lambda: {"claude": "/h/.claude", "prompt": "/h/p.md"},
    )
    monkeypatch.setattr(orch, "_record_spawn_session", _noop)
    monkeypatch.setattr(orch, "_fire_audit", lambda **_k: None)
    # issue_agent_token + AGENTS are imported INSIDE _spawn_secretary_container;
    # patch them at their source so the spec construction doesn't touch crypto /
    # the real agent table.
    monkeypatch.setattr(
        "roboco.agents_config.issue_agent_token", lambda *_a, **_k: "tok"
    )
    monkeypatch.setattr(
        "roboco.foundation.identity.AGENTS",
        {
            SECRETARY_AGENT_ID: SimpleNamespace(
                uuid=UUID("00000000-0000-0000-0000-000000000001")
            )
        },
    )


@pytest.fixture(autouse=True)
def _fresh_registry() -> None:
    """Isolate the process-wide live registry per test."""
    prev = prompter_live._RegistryHolder.instance
    prompter_live._RegistryHolder.instance = prompter_live.PrompterLiveRegistry()
    yield
    prompter_live._RegistryHolder.instance = prev


@pytest.mark.asyncio
async def test_shutdown_mid_spawn_removes_container_and_skips_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """docker run completes, THEN ``_running`` flips to False before the
    registration line. The just-started Secretary container must be removed and
    NOT registered — otherwise it is orphaned (live, untracked by stop())."""
    orch = _make_orchestrator()
    removed: list[str] = []
    _wire_secretary_spawn_mocks(monkeypatch, orch, removed, flip_running_on_run=True)

    registry = prompter_live.get_live_registry()
    pushed: list[tuple[str, dict[str, Any]]] = []
    closed: list[str] = []
    monkeypatch.setattr(registry, "push", lambda sid, ev: pushed.append((sid, ev)))
    monkeypatch.setattr(registry, "close", closed.append)
    registry.open("sess-sec-orphan", SECRETARY_AGENT_ID)

    await orch._spawn_secretary_container_guarded(
        "sess-sec-orphan", initial_message=None
    )

    # Two removes: the pre-spawn reap of any stale container, then the
    # post-docker-run shutdown guard reaping the just-started one. Without the
    # guard there is only ONE remove and the just-started container is orphaned.
    assert removed == [
        f"roboco-agent-{SECRETARY_AGENT_ID}",
        f"roboco-agent-{SECRETARY_AGENT_ID}",
    ]
    # No instance registered — stop()'s _instances iteration has already run.
    assert SECRETARY_AGENT_ID not in orch._instances
    # Shutdown is not a user-facing failure: relay closes silently, no error.
    assert pushed == []
    assert closed == ["sess-sec-orphan"]


@pytest.mark.asyncio
async def test_running_spawn_registers_normally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity: when the orchestrator stays running, the Secretary spawn
    registers the instance as before — the shutdown guard does not fire."""
    orch = _make_orchestrator()
    removed: list[str] = []
    _wire_secretary_spawn_mocks(monkeypatch, orch, removed, flip_running_on_run=False)

    instance = await orch.spawn_secretary_session("sess-sec-ok", initial_message=None)

    assert orch._instances[SECRETARY_AGENT_ID] is instance
    # Only the pre-spawn reap remove — the shutdown guard did NOT remove the
    # just-started container (the orchestrator stayed running).
    assert removed == [f"roboco-agent-{SECRETARY_AGENT_ID}"]
