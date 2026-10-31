"""GEMINI agents capture token usage/cost from their captured ``usage.json``.

A Gemini agent runs the gemini CLI — no SDK /usage/status server and no
Claude transcript — so finalize reads the ``usage.json`` the entrypoint wrote
to the per-agent data dir (mounted into the orchestrator). The capture
(gemini_cli_usage.usage_and_cost) prices each model's real input/output split
at its own rate and writes that split beside grok's ``{model, total_tokens,
cost_usd}`` shape: finalize records the split and the priced cost. A file from
an image that predates the split folds its total into output, as grok's does.
"""

from __future__ import annotations

import json
import tempfile
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from roboco.llm.providers import gemini_cli_usage
from roboco.models.runtime import AgentInstance
from roboco.runtime.engines import spawn_exit as usage_root_mod
from roboco.runtime.orchestrator import AgentOrchestrator

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


# An input-heavy gemini-2.5-pro run ($1.25 in / $10.00 out per 1M tokens), as
# the entrypoint's capture writes it: the real split and its priced cost.
_RUN_TOKENS_INPUT = 900_000
_RUN_TOKENS_OUTPUT = 100_000
_RUN_COST_USD = 2.125
# Expected number of DB execute() calls for a normal finalize (SELECT + UPDATE).
_FINALIZE_EXEC_CALLS = 2


def _write_usage(path: Path, total_tokens: int, cost_usd: float) -> None:
    path.write_text(
        json.dumps(
            {
                "model": "gemini-2.5-pro",
                "total_tokens": total_tokens,
                "cost_usd": cost_usd,
            }
        ),
        encoding="utf-8",
    )


def test_gemini_usage_returns_the_real_split(monkeypatch: pytest.MonkeyPatch) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(
        orch,
        "_gemini_usage_json",
        lambda _aid: {
            "total_tokens": 180,
            "tokens_input": 150,
            "tokens_output": 30,
            "cost_usd": 0.02,
        },
    )

    assert orch._gemini_usage_tokens("be-dev-1") == (150, 30, 0, 0)


def test_gemini_usage_without_a_split_folds_total_into_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    usage = tmp_path / "usage.json"
    _write_usage(usage, total_tokens=180, cost_usd=0.02)
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(
        orch, "_gemini_usage_json", lambda _aid: json.loads(usage.read_text())
    )

    assert orch._gemini_usage_tokens("be-dev-1") == (0, 180, 0, 0)


def test_gemini_usage_zero_when_store_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(orch, "_gemini_usage_json", lambda _aid: None)
    assert orch._gemini_usage_tokens("be-dev-1") == (0, 0, 0, 0)


def test_gemini_usage_cost_reads_the_priced_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_cost = 3.25
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(
        orch,
        "_gemini_usage_json",
        lambda _aid: {"cost_usd": captured_cost, "total_tokens": 9},
    )
    assert orch._gemini_usage_cost("be-dev-1") == captured_cost


def test_gemini_usage_cost_none_when_store_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(orch, "_gemini_usage_json", lambda _aid: None)
    assert orch._gemini_usage_cost("be-dev-1") is None


@pytest.mark.asyncio
async def test_resolve_final_usage_routes_gemini_to_usage_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(
        orch, "_gemini_usage_json", lambda _aid: {"total_tokens": 12, "cost_usd": 0.01}
    )
    cfg = type("C", (), {"provider_type": "gemini"})()
    orch._instances = {"be-dev-1": AgentInstance(agent_id="be-dev-1", config=cfg)}

    assert await orch._resolve_final_token_usage("be-dev-1") == (0, 12, 0, 0)


@pytest.mark.asyncio
async def test_resolve_final_turns_tools_gemini_has_neither() -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    cfg = type("C", (), {"provider_type": "gemini"})()
    orch._instances = {"be-dev-1": AgentInstance(agent_id="be-dev-1", config=cfg)}
    assert await orch._resolve_final_turns_tools("be-dev-1") == (0, 0)


@pytest.mark.asyncio
async def test_resolve_active_tokens_routes_gemini_to_usage_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(
        orch, "_gemini_usage_json", lambda _aid: {"total_tokens": 12, "cost_usd": 0.01}
    )
    cfg = type("C", (), {"provider_type": "gemini"})()
    orch._instances = {"be-dev-1": AgentInstance(agent_id="be-dev-1", config=cfg)}
    async with httpx.AsyncClient() as client:
        assert await orch._resolve_active_tokens(client, "be-dev-1") == (0, 12, 0, 0)


def test_gemini_usage_dir_branches_compose_vs_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(usage_root_mod, "PROJECT_HOST_PATH", "")
    local = AgentOrchestrator._gemini_usage_dir("be-dev-1")
    assert "roboco-gemini-usage" in str(local)
    assert local.name == "be-dev-1"

    monkeypatch.setattr(usage_root_mod, "PROJECT_HOST_PATH", "/volume1/roboco")
    monkeypatch.setattr(usage_root_mod, "GEMINI_USAGE_DATA_DIR", "/data/gemini-usage")
    assert str(AgentOrchestrator._gemini_usage_dir("be-dev-1")) == (
        "/data/gemini-usage/be-dev-1"
    )


@pytest.mark.parametrize(
    "bad",
    ["..", ".", "../etc", "a/b", "a\\b", "", "be-dev-1/../x", "x\x00y"],
)
def test_gemini_usage_dir_rejects_path_traversal(bad: str) -> None:
    with pytest.raises(ValueError, match="unsafe agent id"):
        AgentOrchestrator._gemini_usage_dir(bad)


def test_gemini_usage_json_reads_the_real_local_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The un-mocked read path must find usage.json in the SAME branched dir the
    # writer mounts (mirrors _ensure_gemini_usage_dir's create path).
    monkeypatch.setattr(usage_root_mod, "PROJECT_HOST_PATH", "")
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    udir = tmp_path / "roboco-gemini-usage" / "be-dev-1"
    udir.mkdir(parents=True)
    cost_usd = 0.1
    (udir / "usage.json").write_text(
        json.dumps({"total_tokens": 55, "cost_usd": cost_usd}), encoding="utf-8"
    )
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    assert orch._gemini_usage_tokens("be-dev-1") == (0, 55, 0, 0)
    assert orch._gemini_usage_cost("be-dev-1") == cost_usd


def test_finalize_reads_back_what_the_capture_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The entrypoint's writer and the orchestrator's readers must agree on the
    # file's keys: run the real capture over a stream-json run log into the
    # per-agent dir, then read it back un-mocked.
    monkeypatch.setattr(usage_root_mod, "PROJECT_HOST_PATH", "")
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    run_log = tmp_path / "run.ndjson"
    run_log.write_text(
        json.dumps(
            {
                "type": "result",
                "stats": {
                    "models": {
                        "gemini-2.5-pro": {
                            "input_tokens": _RUN_TOKENS_INPUT,
                            "output_tokens": _RUN_TOKENS_OUTPUT,
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    udir = tmp_path / "roboco-gemini-usage" / "be-dev-1"
    gemini_cli_usage.capture_run_usage(
        run_log=run_log, fallback_model="gemini-2.5-pro", out_path=udir / "usage.json"
    )
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    assert orch._gemini_usage_tokens("be-dev-1") == (
        _RUN_TOKENS_INPUT,
        _RUN_TOKENS_OUTPUT,
        0,
        0,
    )
    assert orch._gemini_usage_cost("be-dev-1") == pytest.approx(_RUN_COST_USD)


def test_ensure_gemini_usage_dir_creates_world_writable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(usage_root_mod, "PROJECT_HOST_PATH", "")
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._ensure_gemini_usage_dir("be-dev-1")
    target = tmp_path / "roboco-gemini-usage" / "be-dev-1"
    assert target.is_dir()


def _gemini_session_instance(agent_id: str = "be-dev-1") -> AgentInstance:
    cfg = type(
        "C",
        (),
        {"provider_type": "gemini", "model": "gemini-2.5-pro", "blueprint_path": None},
    )()
    return AgentInstance(agent_id=agent_id, config=cfg, usage_session_id=uuid4())


def _db_factory_with_row(session_row: Any, execute_calls: list[Any]) -> Any:
    """A get_session_factory stand-in whose session SELECTs back *session_row*
    and records every execute() statement."""

    @asynccontextmanager
    async def _db() -> AsyncIterator[MagicMock]:
        db = MagicMock()
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=session_row)

        async def _exec(stmt: Any) -> MagicMock:
            execute_calls.append(stmt)
            return result

        db.execute = AsyncMock(side_effect=_exec)
        db.commit = AsyncMock()
        yield db

    return _db


async def _finalized_values(
    monkeypatch: pytest.MonkeyPatch, usage: dict[str, object]
) -> dict[str, Any]:
    """Finalize one GEMINI spawn session reading *usage* and return the values
    its UPDATE wrote to agent_spawn_sessions."""
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._instances = {"be-dev-1": _gemini_session_instance()}
    monkeypatch.setattr(orch, "_gemini_usage_json", lambda _aid: usage)
    session_row = MagicMock()
    session_row.id = "sess-1"
    execute_calls: list[Any] = []
    with patch(
        "roboco.db.base.get_session_factory",
        return_value=_db_factory_with_row(session_row, execute_calls),
    ):
        await orch._finalize_spawn_session("be-dev-1", exit_reason="completed")
    assert len(execute_calls) == _FINALIZE_EXEC_CALLS
    params: dict[str, Any] = execute_calls[1].compile().params
    return params


@pytest.mark.asyncio
async def test_finalize_records_the_gemini_split_and_its_priced_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The session keeps the run's real input/output split and the cost the
    capture priced from it, not every token re-priced at the output rate
    ($10.00 for this run instead of $2.125)."""
    values = await _finalized_values(
        monkeypatch,
        {
            "model": "gemini-2.5-pro",
            "total_tokens": _RUN_TOKENS_INPUT + _RUN_TOKENS_OUTPUT,
            "tokens_input": _RUN_TOKENS_INPUT,
            "tokens_output": _RUN_TOKENS_OUTPUT,
            "cost_usd": _RUN_COST_USD,
        },
    )
    assert values["tokens_input"] == _RUN_TOKENS_INPUT
    assert values["tokens_output"] == _RUN_TOKENS_OUTPUT
    assert values["estimated_cost_usd"] == pytest.approx(_RUN_COST_USD)


@pytest.mark.asyncio
async def test_finalize_takes_a_pre_split_gemini_file_cost_as_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A usage.json from an image that predates the split carries only the
    total, which folds into output, but its cost was still priced from the
    real split, so that cost is recorded rather than the output-rate one."""
    values = await _finalized_values(
        monkeypatch,
        {
            "model": "gemini-2.5-pro",
            "total_tokens": _RUN_TOKENS_INPUT + _RUN_TOKENS_OUTPUT,
            "cost_usd": _RUN_COST_USD,
        },
    )
    assert values["tokens_input"] == 0
    assert values["tokens_output"] == _RUN_TOKENS_INPUT + _RUN_TOKENS_OUTPUT
    assert values["estimated_cost_usd"] == pytest.approx(_RUN_COST_USD)
