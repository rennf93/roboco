"""GROK agents capture token usage/cost from their captured ``usage.json``.

A Grok agent runs the grok CLI — no SDK /usage/status server and no Claude
transcript — so finalize reads the ``usage.json`` the entrypoint / interactive
driver wrote to the per-agent data dir (mounted into the orchestrator). grok
reports a single cumulative total with no input/output split, so it folds into
output (it bills at the output rate).
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
            {"model": "grok-build", "total_tokens": total_tokens, "cost_usd": cost_usd}
        ),
        encoding="utf-8",
    )


def test_grok_usage_folds_total_into_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    usage = tmp_path / "usage.json"
    _write_usage(usage, total_tokens=180, cost_usd=0.02)
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(
        orch, "_grok_usage_json", lambda _aid: json.loads(usage.read_text())
    )

    # The whole total folds into output (no input/output split from the CLI).
    assert orch._grok_usage_tokens("be-dev-1") == (0, 180, 0, 0)


def test_grok_usage_zero_when_store_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(orch, "_grok_usage_json", lambda _aid: None)
    assert orch._grok_usage_tokens("be-dev-1") == (0, 0, 0, 0)


def test_grok_cost_read_from_usage_json(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_cost = 3.25
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(
        orch,
        "_grok_usage_json",
        lambda _aid: {"cost_usd": captured_cost, "total_tokens": 9},
    )
    assert orch._grok_cost_usd("be-dev-1") == captured_cost
    monkeypatch.setattr(orch, "_grok_usage_json", lambda _aid: None)
    assert orch._grok_cost_usd("be-dev-1") == 0.0


@pytest.mark.asyncio
async def test_resolve_final_usage_routes_grok_to_usage_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(
        orch, "_grok_usage_json", lambda _aid: {"total_tokens": 12, "cost_usd": 0.01}
    )
    cfg = type("C", (), {"provider_type": "grok"})()
    orch._instances = {"be-dev-1": AgentInstance(agent_id="be-dev-1", config=cfg)}

    # No SDK fetch / transcript read for GROK — usage comes from usage.json.
    assert await orch._resolve_final_token_usage("be-dev-1") == (0, 12, 0, 0)


@pytest.mark.asyncio
async def test_resolve_active_tokens_routes_grok_to_usage_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(
        orch, "_grok_usage_json", lambda _aid: {"total_tokens": 12, "cost_usd": 0.01}
    )
    cfg = type("C", (), {"provider_type": "grok"})()
    orch._instances = {"be-dev-1": AgentInstance(agent_id="be-dev-1", config=cfg)}
    # The client is unused for GROK; pass a bare AsyncClient (never awaited).
    async with httpx.AsyncClient() as client:
        assert await orch._resolve_active_tokens(client, "be-dev-1") == (0, 12, 0, 0)


def test_grok_usage_dir_branches_compose_vs_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(orch_mod, "PROJECT_HOST_PATH", "")
    local = AgentOrchestrator._grok_usage_dir("be-dev-1")
    assert "roboco-grok-usage" in str(local)
    assert local.name == "be-dev-1"

    monkeypatch.setattr(orch_mod, "PROJECT_HOST_PATH", "/volume1/roboco")
    monkeypatch.setattr(orch_mod, "GROK_USAGE_DATA_DIR", "/data/grok-usage")
    assert str(AgentOrchestrator._grok_usage_dir("be-dev-1")) == (
        "/data/grok-usage/be-dev-1"
    )


@pytest.mark.parametrize(
    "bad",
    ["..", ".", "../etc", "a/b", "a\\b", "", "be-dev-1/../x", "x\x00y"],
)
def test_grok_usage_dir_rejects_path_traversal(bad: str) -> None:
    # agent_id reaches the usage dir from request-facing call sites; a value that
    # could traverse the path must be rejected, not used to build a Path.
    with pytest.raises(ValueError, match="unsafe agent id"):
        AgentOrchestrator._grok_usage_dir(bad)


def test_safe_agent_path_segment_accepts_real_slugs() -> None:
    for slug in ("be-dev-1", "pr-reviewer-1", "main-pm", "intake", "secretary"):
        assert AgentOrchestrator._safe_agent_path_segment(slug) == slug


def test_grok_usage_json_reads_the_real_local_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The un-mocked read path must find usage.json in the SAME branched dir the
    # writer mounts (the local-mode fix: read side mirrors the write side).
    monkeypatch.setattr(orch_mod, "PROJECT_HOST_PATH", "")
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    udir = tmp_path / "roboco-grok-usage" / "be-dev-1"
    udir.mkdir(parents=True)
    (udir / "usage.json").write_text(
        json.dumps({"total_tokens": 55, "cost_usd": 0.1}), encoding="utf-8"
    )
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    assert orch._grok_usage_tokens("be-dev-1") == (0, 55, 0, 0)
    assert orch._grok_cost_usd("be-dev-1") == 0.1  # noqa: PLR2004
