"""``_institutional_memory``'s RAG search is bounded by
``institutional_memory_timeout_seconds`` — a saturated Ollama embedder must
never eat the whole verb timeout budget and 504 a claim. See
``roboco.services.gateway.choreographer._impl.Choreographer._institutional_memory``.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.config import Settings
from roboco.services.gateway.choreographer import Choreographer

_DEFAULT_TIMEOUT = 8.0
_OVERRIDE_TIMEOUT = 3.5


def _choreographer(*, similar_memory: AsyncMock) -> Choreographer:
    repo = AsyncMock()
    repo.similar_memory = similar_memory
    task_svc = AsyncMock()
    task_svc.agent_for.return_value = MagicMock(role="developer")
    choreo = object.__new__(Choreographer)
    choreo._deps = MagicMock(evidence_repo=repo, task=task_svc)
    return choreo


def _task() -> MagicMock:
    return MagicMock(
        id=uuid4(), title="Add retry backoff", task_type=MagicMock(value="code")
    )


class TestInstitutionalMemoryTimeout:
    @pytest.mark.asyncio
    async def test_slow_search_times_out_without_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("roboco.config.settings.org_memory_enabled", True)
        monkeypatch.setattr(
            "roboco.config.settings.institutional_memory_timeout_seconds", 0.01
        )

        async def _slow(**_kwargs: object) -> dict[str, object]:
            await asyncio.sleep(5)
            return {"items": [], "status": "ok"}

        choreo = _choreographer(similar_memory=AsyncMock(side_effect=_slow))
        result = await choreo._institutional_memory(uuid4(), _task())
        assert result == {"status": "timeout", "lessons": []}

    @pytest.mark.asyncio
    async def test_fast_search_flows_through_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("roboco.config.settings.org_memory_enabled", True)
        monkeypatch.setattr(
            "roboco.config.settings.institutional_memory_timeout_seconds", 8.0
        )
        lesson = {"kind": "learning", "summary": "s", "source": "src", "score": 0.9}
        similar_memory = AsyncMock(return_value={"items": [lesson], "status": "ok"})
        choreo = _choreographer(similar_memory=similar_memory)
        result = await choreo._institutional_memory(uuid4(), _task())
        assert result == {"status": "ok", "lessons": [lesson]}


class TestInstitutionalMemoryTimeoutConfig:
    def test_default_is_eight_seconds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ROBOCO_INSTITUTIONAL_MEMORY_TIMEOUT_SECONDS", raising=False)
        assert Settings().institutional_memory_timeout_seconds == _DEFAULT_TIMEOUT

    def test_reads_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "ROBOCO_INSTITUTIONAL_MEMORY_TIMEOUT_SECONDS", str(_OVERRIDE_TIMEOUT)
        )
        assert Settings().institutional_memory_timeout_seconds == _OVERRIDE_TIMEOUT
