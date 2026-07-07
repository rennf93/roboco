"""_sweep_token_snapshots prices cache tokens, not just input/output.

The live USAGE_SNAPSHOT cost must match calculate_cost over the full 4-tuple
(the finalize path already does); dropping cache read/write undercounts
Anthropic cache spend mid-run.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import roboco.db.base as db_base
from roboco.billing import pricing
from roboco.models.runtime import AgentInstance
from roboco.runtime.orchestrator import AgentOrchestrator, AgentState

# 1M input, 0 output, 4M cache read, 0 cache write — cache spend dominates.
TOKENS_INPUT = 1_000_000
TOKENS_CACHE_READ = 4_000_000


def _active_orch() -> tuple[AgentOrchestrator, AgentInstance]:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    cfg = type("C", (), {"provider_type": "anthropic", "model": "claude-sonnet-5"})()
    inst = AgentInstance(agent_id="be-dev-1", state=AgentState.ACTIVE, config=cfg)
    inst.container_id = "cid"
    orch._instances = {"be-dev-1": inst}
    return orch, inst


@pytest.mark.asyncio
async def test_sweep_passes_cache_tokens_to_calculate_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch, _inst = _active_orch()
    captured: dict[str, Any] = {}

    def fake_calc(
        model: str,
        tokens_input: int,
        tokens_output: int,
        tokens_cache_read: int = 0,
        tokens_cache_write: int = 0,
    ) -> float:
        captured.update(
            model=model,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            tokens_cache_read=tokens_cache_read,
            tokens_cache_write=tokens_cache_write,
        )
        return 0.804  # 4M * 0.201/1M cache-read spend

    monkeypatch.setattr(pricing, "calculate_cost", fake_calc)
    monkeypatch.setattr(
        orch,
        "_resolve_active_tokens",
        AsyncMock(return_value=(TOKENS_INPUT, 0, TOKENS_CACHE_READ, 0)),
    )
    monkeypatch.setattr(
        orch,
        "_persist_token_snapshot",
        AsyncMock(return_value=True),
    )
    # Stub the session factory import path the sweep uses.
    fake_session = AsyncMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=fake_session)
    monkeypatch.setattr(db_base, "get_session_factory", lambda: factory)
    # No publish target needed; the post-loop publish is best-effort.
    monkeypatch.setattr(orch, "_publish_usage_snapshot", AsyncMock(), raising=False)

    await orch._sweep_token_snapshots()

    assert captured["tokens_cache_read"] == TOKENS_CACHE_READ
    assert captured["tokens_cache_write"] == 0
    assert captured["tokens_input"] == TOKENS_INPUT
