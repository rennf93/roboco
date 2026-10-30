"""HUMMIN per-provider spawn concurrency cap.

Unlike kimi, hummin has NO shared credential chain to protect — auth is a
static per-spawn env key (ZAI_API_KEY) — so ``hummin_max_concurrent``
defaults to ``None`` (uncapped). The Settings knob exists only for an
operator who observes Z.ai throttling the GLM Coding Plan account; this
test pins both halves of that contract: default-uncapped, cap-honored when
set (enforced in ``AgentOrchestrator._provider_concurrency_cap``, the same
chokepoint kimi's cap uses).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from roboco.config import settings
from roboco.models.base import ModelProvider
from roboco.runtime.orchestrator import AgentOrchestrator

if TYPE_CHECKING:
    import pytest

_HUMMIN_CAP = 2


def test_hummin_is_uncapped_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "hummin_max_concurrent", None)
    assert (
        AgentOrchestrator._provider_concurrency_cap(ModelProvider.HUMMIN.value) is None
    )


def test_hummin_cap_honored_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "hummin_max_concurrent", _HUMMIN_CAP)
    assert AgentOrchestrator._provider_concurrency_cap("hummin") == _HUMMIN_CAP


def test_other_providers_stay_uncapped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "hummin_max_concurrent", _HUMMIN_CAP)
    assert AgentOrchestrator._provider_concurrency_cap("anthropic") is None
    assert AgentOrchestrator._provider_concurrency_cap(None) is None


def test_hummin_at_capacity_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """The generic capacity gate reads the hummin cap like any provider's."""
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(settings, "hummin_max_concurrent", 1)
    monkeypatch.setattr(
        orch,
        "_live_provider_instance_count",
        lambda _provider_type, _exclude_agent_id=None: 1,
    )
    assert orch._provider_spawn_at_capacity("hummin") is True
    monkeypatch.setattr(settings, "hummin_max_concurrent", None)
    assert orch._provider_spawn_at_capacity("hummin") is False
