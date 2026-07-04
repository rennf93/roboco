"""Fable-mode ships doctrine + hooks together at a single agent spawn.

Task 3's and Task 6's unit tests each prove their own half in isolation:
compose_prompt includes the doctrine layer; _generate_agent_settings injects
the hook groups. Neither proves the two halves land together for the SAME
spawn. This is that proof, using the orchestrator's own
_generate_composed_prompt + _generate_agent_settings directly — the plan's
named lighter-weight alternative to _prepare_agent_spawn, which needs a live
DB session, docker, and a real workspace/worktree and so is not a fit for a
fast integration test (see the plan doc under docs/superpowers/plans/,
Task 11).
"""

from __future__ import annotations

import json
from unittest.mock import patch

from roboco.runtime.orchestrator import AgentOrchestrator

_WS = "/data/workspaces/roboco-api/backend/be-dev-1"
_CELL = "/data/workspaces/roboco-api/backend"


def _orch() -> AgentOrchestrator:
    with patch.object(AgentOrchestrator, "__init__", return_value=None):
        return AgentOrchestrator.__new__(AgentOrchestrator)


def test_flag_on_ships_doctrine_and_hooks_together() -> None:
    with patch("roboco.config.settings.fable_mode_enabled", True):
        orch = _orch()
        prompt_path = orch._generate_composed_prompt("be-dev-1")
        settings_path = orch._generate_agent_settings(
            agent_id="be-dev-1",
            role="developer",
            workspace_path=_WS,
            cell_workspace_path=_CELL,
        )
    prompt = prompt_path.read_text()
    hooks = json.loads(settings_path.read_text())["hooks"]
    assert "# Fable Doctrine" in prompt
    stop_cmds = [h["command"] for g in hooks["Stop"] for h in g["hooks"]]
    assert "/app/scripts/fable-stop-gate-hook.sh" in stop_cmds


def test_flag_off_ships_neither() -> None:
    with patch("roboco.config.settings.fable_mode_enabled", False):
        orch = _orch()
        prompt_path = orch._generate_composed_prompt("be-dev-1")
        settings_path = orch._generate_agent_settings(
            agent_id="be-dev-1",
            role="developer",
            workspace_path=_WS,
            cell_workspace_path=_CELL,
        )
    prompt = prompt_path.read_text()
    hooks = json.loads(settings_path.read_text())["hooks"]
    assert "# Fable Doctrine" not in prompt
    stop_cmds = [h["command"] for g in hooks["Stop"] for h in g["hooks"]]
    assert not any("fable" in c for c in stop_cmds)
