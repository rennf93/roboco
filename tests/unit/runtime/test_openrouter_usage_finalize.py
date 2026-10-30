"""OPENROUTER agents capture real 4-bucket token usage + metered cost from
their captured ``usage.json`` (see ``openrouter_cli_usage``): finalize must
return the genuine split (the codex/kimi shape) and must attribute spend from
OpenRouter's own metered ``cost`` field, never by re-pricing tokens through
the static pricing table (it cannot know OpenRouter's catalog).
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
from roboco.models.runtime import AgentInstance
from roboco.runtime.engines import interactive_sessions as usage_root_mod
from roboco.runtime.orchestrator import AgentOrchestrator

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

# Token bucket counts used across payload + finalize assertions (PLR2004).
_TIN = 12
_TOUT = 34
_TCR = 5
_TCW = 1
# Expected number of DB execute() calls for a normal finalize (SELECT + UPDATE).
_FINALIZE_EXEC_CALLS = 2

_METERED_COST = 0.137


def _write_usage(path: Path, **fields: object) -> None:
    payload = {
        "model": "anthropic/claude-sonnet-5",
        "tokens_input": 0,
        "tokens_output": 0,
        "tokens_cache_read": 0,
        "tokens_cache_write": 0,
        "cost_usd": 0.0,
        **fields,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _usage_payload() -> dict[str, object]:
    return {
        "model": "anthropic/claude-sonnet-5",
        "tokens_input": _TIN,
        "tokens_output": _TOUT,
        "tokens_cache_read": _TCR,
        "tokens_cache_write": _TCW,
        "cost_usd": _METERED_COST,
    }


def _openrouter_instance(agent_id: str = "be-dev-1") -> AgentInstance:
    cfg = type(
        "C", (), {"provider_type": "openrouter", "model": "anthropic/claude-sonnet-5"}
    )()
    return AgentInstance(agent_id=agent_id, config=cfg)


def test_openrouter_usage_returns_real_split(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    usage = tmp_path / "usage.json"
    _write_usage(
        usage,
        tokens_input=300,
        tokens_output=130,
        tokens_cache_read=30,
        tokens_cache_write=7,
    )
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(
        orch, "_openrouter_usage_json", lambda _aid: json.loads(usage.read_text())
    )

    assert orch._openrouter_usage_tokens("be-dev-1") == (300, 130, 30, 7)


def test_openrouter_usage_zero_when_store_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(orch, "_openrouter_usage_json", lambda _aid: None)
    assert orch._openrouter_usage_tokens("be-dev-1") == (0, 0, 0, 0)


def test_openrouter_usage_cost_reads_metered_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The spend IS usage.json's metered ``cost_usd`` (never re-priced)."""
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(orch, "_openrouter_usage_json", lambda _aid: _usage_payload())
    assert orch._openrouter_usage_cost("be-dev-1") == pytest.approx(_METERED_COST)


def test_openrouter_usage_cost_none_when_store_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(orch, "_openrouter_usage_json", lambda _aid: None)
    assert orch._openrouter_usage_cost("be-dev-1") is None


@pytest.mark.asyncio
async def test_resolve_final_usage_routes_openrouter_to_usage_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(
        orch,
        "_openrouter_usage_json",
        lambda _aid: {
            "tokens_input": 12,
            "tokens_output": 34,
            "tokens_cache_read": 5,
            "tokens_cache_write": 1,
        },
    )
    orch._instances = {"be-dev-1": _openrouter_instance()}

    assert await orch._resolve_final_token_usage("be-dev-1") == (12, 34, 5, 1)


@pytest.mark.asyncio
async def test_resolve_final_turns_tools_openrouter_has_no_turn_signal() -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._instances = {"be-dev-1": _openrouter_instance()}

    # OpenRouter's capture has no per-turn signal (the grok/gemini shape).
    assert await orch._resolve_final_turns_tools("be-dev-1") == (0, 0)


@pytest.mark.asyncio
async def test_resolve_active_tokens_routes_openrouter_to_usage_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(
        orch,
        "_openrouter_usage_json",
        lambda _aid: {"tokens_input": 12, "tokens_output": 34},
    )
    orch._instances = {"be-dev-1": _openrouter_instance()}
    async with httpx.AsyncClient() as client:
        assert await orch._resolve_active_tokens(client, "be-dev-1") == (12, 34, 0, 0)


def test_openrouter_usage_dir_branches_compose_vs_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(usage_root_mod, "PROJECT_HOST_PATH", "")
    local = AgentOrchestrator._openrouter_usage_dir("be-dev-1")
    assert "roboco-openrouter-usage" in str(local)
    assert local.name == "be-dev-1"

    monkeypatch.setattr(usage_root_mod, "PROJECT_HOST_PATH", "/volume1/roboco")
    monkeypatch.setattr(
        usage_root_mod, "OPENROUTER_USAGE_DATA_DIR", "/data/openrouter-usage"
    )
    assert str(AgentOrchestrator._openrouter_usage_dir("be-dev-1")) == (
        "/data/openrouter-usage/be-dev-1"
    )


@pytest.mark.parametrize(
    "bad",
    ["..", ".", "../etc", "a/b", "a\\b", "", "be-dev-1/../x", "x\x00y"],
)
def test_openrouter_usage_dir_rejects_path_traversal(bad: str) -> None:
    with pytest.raises(ValueError, match="unsafe agent id"):
        AgentOrchestrator._openrouter_usage_dir(bad)


def test_openrouter_usage_json_reads_the_real_local_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(usage_root_mod, "PROJECT_HOST_PATH", "")
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    udir = tmp_path / "roboco-openrouter-usage" / "be-dev-1"
    udir.mkdir(parents=True)
    _write_usage(udir / "usage.json", tokens_input=55, tokens_output=10)
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    assert orch._openrouter_usage_tokens("be-dev-1") == (55, 10, 0, 0)


# ---------------------------------------------------------------------------
# Finalize end-to-end: the DB row carries the 4 buckets + the metered cost.
# ---------------------------------------------------------------------------


def _openrouter_session_instance(agent_id: str = "be-dev-1") -> AgentInstance:
    cfg = type(
        "C",
        (),
        {
            "provider_type": "openrouter",
            "model": "anthropic/claude-sonnet-5",
            "blueprint_path": None,
        },
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


@pytest.mark.asyncio
async def test_finalize_spawn_session_maps_buckets_and_metered_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All 4 token buckets land on agent_spawn_sessions, and estimated_cost_usd
    comes from usage.cost, NOT calculate_cost (spend must not be re-priced)."""
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._instances = {"be-dev-1": _openrouter_session_instance()}
    monkeypatch.setattr(orch, "_openrouter_usage_json", lambda _aid: _usage_payload())

    session_row = MagicMock()
    session_row.id = "sess-1"
    execute_calls: list[Any] = []

    with (
        patch(
            "roboco.db.base.get_session_factory",
            return_value=_db_factory_with_row(session_row, execute_calls),
        ),
        patch("roboco.billing.pricing.calculate_cost") as mock_cost,
    ):
        await orch._finalize_spawn_session("be-dev-1", exit_reason="completed")

    mock_cost.assert_not_called()
    # SELECT (find the row) + UPDATE (write the values).
    assert len(execute_calls) == _FINALIZE_EXEC_CALLS
    values = execute_calls[1].compile().params
    assert values["tokens_input"] == _TIN
    assert values["tokens_output"] == _TOUT
    assert values["tokens_cache_read"] == _TCR
    assert values["tokens_cache_write"] == _TCW
    assert values["estimated_cost_usd"] == pytest.approx(_METERED_COST)
    assert values["exit_reason"] == "completed"


@pytest.mark.asyncio
async def test_finalize_spawn_session_falls_back_to_calculate_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing usage.json leaves cost resolution to calculate_cost (the
    provider-agnostic path) - finalize must never raise on a missing read."""
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._instances = {"be-dev-1": _openrouter_session_instance()}
    monkeypatch.setattr(orch, "_openrouter_usage_json", lambda _aid: None)

    session_row = MagicMock()
    session_row.id = "sess-1"
    execute_calls: list[Any] = []

    with (
        patch(
            "roboco.db.base.get_session_factory",
            return_value=_db_factory_with_row(session_row, execute_calls),
        ),
        patch("roboco.billing.pricing.calculate_cost", return_value=0.0) as mock_cost,
    ):
        await orch._finalize_spawn_session("be-dev-1", exit_reason="crashed")

    mock_cost.assert_called_once()
    values = execute_calls[1].compile().params
    assert values["estimated_cost_usd"] == 0.0
    assert values["exit_reason"] == "crashed"
