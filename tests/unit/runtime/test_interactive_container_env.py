"""Intake / Secretary `docker run`: env values ride the CLI env, not argv."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator


@pytest.mark.asyncio
async def test_run_container_cmd_moves_env_values_into_cli_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(b"cid-123\n", b""))
    proc.returncode = 0
    exec_mock = AsyncMock(return_value=proc)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", exec_mock)
    orch = AgentOrchestrator.__new__(AgentOrchestrator)

    container_id = await orch._run_container_cmd(
        [
            "docker",
            "run",
            "-d",
            "-e",
            "ANTHROPIC_AUTH_TOKEN=sk-secret",
            "-e",
            "ROBOCO_AGENT_TOKEN=tok-secret",
            "roboco-agent-prompter",
        ]
    )

    assert container_id == "cid-123"
    assert list(exec_mock.call_args.args) == [
        "docker",
        "run",
        "-d",
        "-e",
        "ANTHROPIC_AUTH_TOKEN",
        "-e",
        "ROBOCO_AGENT_TOKEN",
        "roboco-agent-prompter",
    ]
    env = exec_mock.call_args.kwargs["env"]
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-secret"
    assert env["ROBOCO_AGENT_TOKEN"] == "tok-secret"
