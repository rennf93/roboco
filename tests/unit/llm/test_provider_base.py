"""AgentProvider interactive scaffolding: opt-in, declines by default."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from roboco.llm.providers.base import (
    AgentProvider,
    InteractiveSpawnSpec,
    ProviderError,
    SpawnResult,
)

if TYPE_CHECKING:
    from pathlib import Path

    from roboco.models.runtime import OrchestratorAgentConfig as AgentConfig


class _OneShotOnly(AgentProvider):
    """A minimal provider that implements only the one-shot lifecycle."""

    async def spawn(
        self,
        config: AgentConfig,
        initial_prompt: str | None = None,
        agent_settings_path: Path | None = None,
    ) -> SpawnResult:
        return SpawnResult(instance_id="x")

    async def stop(self, instance_id: str, graceful: bool = True) -> None: ...

    async def health_check(self, instance_id: str) -> bool:
        return True

    async def remove(self, instance_id: str) -> None: ...


def _spec() -> InteractiveSpawnSpec:
    cfg = type("C", (), {"agent_id": "intake-1"})()
    return InteractiveSpawnSpec(
        config=cfg, image="img", session_id="s1", role="prompter"
    )


def test_provider_declines_interactive_by_default() -> None:
    assert _OneShotOnly.supports_interactive is False


@pytest.mark.asyncio
async def test_spawn_interactive_default_raises() -> None:
    # A one-shot-only provider declines interactive spawns rather than crashing.
    with pytest.raises(ProviderError):
        await _OneShotOnly().spawn_interactive(_spec())
