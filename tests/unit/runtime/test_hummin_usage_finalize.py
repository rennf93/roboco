"""HUMMIN agents capture real input/output/cache-split token usage from their
captured ``usage.json`` — hummin's ``--mode json`` run log carries a genuine,
already-disjoint 4-bucket split summed across every assistant ``message_end``
(see ``hummin_cli_usage``), so finalize must return the real 4-tuple instead
of folding everything into output.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import pytest
from roboco.models.runtime import AgentInstance
from roboco.runtime.engines import spawn_exit as spawn_exit_module
from roboco.runtime.orchestrator import AgentOrchestrator

if TYPE_CHECKING:
    from pathlib import Path

    import httpx


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


def test_hummin_usage_zero_read_warns_only_at_finalize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The zero-read warning is a FINALIZE signal: the per-tick sweep sampler
    reads the same usage.json with warn_on_zero=False (a zero mid-run is the
    expected state for a one-shot CLI whose capture is written post-run), so
    the warning must fire only on the default (finalize) call, and it must
    name the path it tried, not just the mount."""
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(orch, "_hummin_usage_json", lambda _aid: None)
    seen: list[dict] = []

    class _SpyLogger:
        def warning(self, event: str, **kw: object) -> None:
            seen.append({"event": event, **kw})

    monkeypatch.setattr(spawn_exit_module, "logger", _SpyLogger())

    assert orch._hummin_usage_tokens("fe-qa") == (0, 0, 0, 0)
    assert len(seen) == 1
    assert "usage_path" in seen[0]

    assert orch._hummin_usage_tokens("fe-qa", warn_on_zero=False) == (0, 0, 0, 0)
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_active_token_sweep_reads_usage_json_quietly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sweep sampler's usage.json read passes warn_on_zero=False: the
    live snapshot treats a zero as 'no data yet', never as an anomaly."""
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    seen: dict[str, object] = {}

    def _quiet_tokens(
        agent_id: str, warn_on_zero: bool = True
    ) -> tuple[int, int, int, int]:
        seen["agent_id"] = agent_id
        seen["warn_on_zero"] = warn_on_zero
        return (0, 0, 0, 0)

    monkeypatch.setattr(orch, "_hummin_usage_tokens", _quiet_tokens)
    cfg = type("C", (), {"provider_type": "hummin"})()
    orch._instances = {"fe-qa": AgentInstance(agent_id="fe-qa", config=cfg)}

    result = await orch._resolve_active_tokens(
        cast("httpx.AsyncClient", None),  # usage-json route never touches the client
        "fe-qa",
    )
    assert result is None
    assert seen == {"agent_id": "fe-qa", "warn_on_zero": False}
