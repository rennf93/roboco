"""HUMMIN agents capture real input/output/cache-split token usage from their
captured ``usage.json`` — hummin's ``--mode json`` run log carries a genuine,
already-disjoint 4-bucket split summed across every assistant ``message_end``
(see ``hummin_cli_usage``), so finalize must return the real 4-tuple instead
of folding everything into output.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from roboco.models.runtime import AgentInstance
from roboco.runtime.orchestrator import AgentOrchestrator

if TYPE_CHECKING:
    from pathlib import Path


def _write_usage(path: Path, **fields: object) -> None:
    payload = {
        "model": "glm-5.3",
        "tokens_input": 0,
        "tokens_output": 0,
        "tokens_cache_read": 0,
        "tokens_cache_write": 0,
        "cost_usd": 0.0,
        "turns": 1,
        **fields,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_hummin_usage_returns_real_split(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    usage = tmp_path / "usage.json"
    _write_usage(
        usage, tokens_input=300, tokens_output=130, tokens_cache_read=30, turns=2
    )
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(
        orch, "_hummin_usage_json", lambda _aid: json.loads(usage.read_text())
    )

    expected_turns = 2
    assert orch._hummin_usage_tokens("be-dev-1") == (300, 130, 30, 0)
    assert orch._hummin_usage_turns("be-dev-1") == expected_turns


def test_hummin_usage_zero_when_store_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(orch, "_hummin_usage_json", lambda _aid: None)
    assert orch._hummin_usage_tokens("be-dev-1") == (0, 0, 0, 0)
    assert orch._hummin_usage_turns("be-dev-1") == 0


@pytest.mark.asyncio
async def test_resolve_final_usage_routes_hummin_to_usage_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(
        orch,
        "_hummin_usage_json",
        lambda _aid: {
            "tokens_input": 12,
            "tokens_output": 34,
            "tokens_cache_read": 5,
            "tokens_cache_write": 1,
        },
    )
    cfg = type("C", (), {"provider_type": "hummin"})()
    orch._instances = {"be-dev-1": AgentInstance(agent_id="be-dev-1", config=cfg)}

    assert await orch._resolve_final_token_usage("be-dev-1") == (12, 34, 5, 1)


@pytest.mark.asyncio
async def test_resolve_final_turns_tools_routes_hummin_to_usage_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(orch, "_hummin_usage_turns", lambda _aid: 3)
    cfg = type("C", (), {"provider_type": "hummin"})()
    orch._instances = {"be-dev-1": AgentInstance(agent_id="be-dev-1", config=cfg)}

    turns, tool_calls = await orch._resolve_final_turns_tools("be-dev-1")
    assert (turns, tool_calls) == (3, 0)
