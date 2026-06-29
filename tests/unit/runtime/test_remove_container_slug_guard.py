"""_remove_container validates the slug before building the log-dir path.

The slug (``container_name`` with the ``roboco-agent-`` prefix stripped)
flows into ``Path("/data/logs/agents") / slug`` and ``mkdir(parents=True)``.
A traversal-shaped slug (``../../etc``) would mkdir outside the logs root.
``spawn_agent`` already validates the agent_id the container name is derived
from, so this is defense-in-depth — but the guard must fire before the join.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator


def _make_orchestrator() -> AgentOrchestrator:
    with patch.object(AgentOrchestrator, "__init__", return_value=None):
        orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._instances = {}
    orch._lock = MagicMock()
    return orch


class _FakeProc:
    async def wait(self) -> int:
        return 0


@pytest.mark.asyncio
async def test_remove_container_skips_log_dump_on_traversal_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A traversal-shaped slug must NOT mkdir outside /data/logs/agents."""
    orch = _make_orchestrator()

    mkdir_paths: list[str] = []

    def _fake_mkdir(self: Path, *_a: object, **_kw: object) -> None:
        mkdir_paths.append(str(self))

    monkeypatch.setattr(Path, "mkdir", _fake_mkdir)
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec", AsyncMock(return_value=_FakeProc())
    )

    # No exception propagates: log-dump is best-effort; removal still runs.
    await orch._remove_container("roboco-agent-../../etc")

    assert mkdir_paths == [], f"traversal slug must not reach mkdir; got {mkdir_paths}"


@pytest.mark.asyncio
async def test_remove_container_normal_slug_dumps_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A normal slug passes the guard and reaches the log-dir mkdir."""
    orch = _make_orchestrator()

    mkdir_paths: list[str] = []

    def _fake_mkdir(self: Path, *_a: object, **_kw: object) -> None:
        mkdir_paths.append(str(self))

    monkeypatch.setattr(Path, "mkdir", _fake_mkdir)
    monkeypatch.setattr(
        "asyncio.create_subprocess_exec", AsyncMock(return_value=_FakeProc())
    )

    await orch._remove_container("roboco-agent-be-dev-1")

    assert any(p.endswith("/data/logs/agents/be-dev-1") for p in mkdir_paths)
