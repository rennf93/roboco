"""GEMINI agents capture token usage/cost from their captured ``usage.json``.

A Gemini agent runs the gemini CLI — no SDK /usage/status server and no
Claude transcript — so finalize reads the ``usage.json`` the entrypoint wrote
to the per-agent data dir (mounted into the orchestrator). Mirrors
test_grok_usage_finalize.py; gemini's usage.json is priced per-model
server-side (gemini_cli_usage.usage_and_cost) but flattens to the SAME
``{model, total_tokens, cost_usd}`` shape, so the read side is identical to
grok's: the whole total folds into output.
"""

from __future__ import annotations

import json
import tempfile
from typing import TYPE_CHECKING

import httpx
import pytest
from roboco.models.runtime import AgentInstance
from roboco.runtime import orchestrator as orch_mod
from roboco.runtime.orchestrator import AgentOrchestrator

if TYPE_CHECKING:
    from pathlib import Path


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


def test_gemini_usage_folds_total_into_output(
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


def test_gemini_cost_read_from_usage_json(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_cost = 3.25
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(
        orch,
        "_gemini_usage_json",
        lambda _aid: {"cost_usd": captured_cost, "total_tokens": 9},
    )
    assert orch._gemini_cost_usd("be-dev-1") == captured_cost
    monkeypatch.setattr(orch, "_gemini_usage_json", lambda _aid: None)
    assert orch._gemini_cost_usd("be-dev-1") == 0.0


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
    monkeypatch.setattr(orch_mod, "PROJECT_HOST_PATH", "")
    local = AgentOrchestrator._gemini_usage_dir("be-dev-1")
    assert "roboco-gemini-usage" in str(local)
    assert local.name == "be-dev-1"

    monkeypatch.setattr(orch_mod, "PROJECT_HOST_PATH", "/volume1/roboco")
    monkeypatch.setattr(orch_mod, "GEMINI_USAGE_DATA_DIR", "/data/gemini-usage")
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
    monkeypatch.setattr(orch_mod, "PROJECT_HOST_PATH", "")
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    udir = tmp_path / "roboco-gemini-usage" / "be-dev-1"
    udir.mkdir(parents=True)
    (udir / "usage.json").write_text(
        json.dumps({"total_tokens": 55, "cost_usd": 0.1}), encoding="utf-8"
    )
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    assert orch._gemini_usage_tokens("be-dev-1") == (0, 55, 0, 0)
    assert orch._gemini_cost_usd("be-dev-1") == 0.1  # noqa: PLR2004


def test_ensure_gemini_usage_dir_creates_world_writable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(orch_mod, "PROJECT_HOST_PATH", "")
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._ensure_gemini_usage_dir("be-dev-1")
    target = tmp_path / "roboco-gemini-usage" / "be-dev-1"
    assert target.is_dir()
