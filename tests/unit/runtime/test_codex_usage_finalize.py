"""OPENAI (codex) agents capture real input/output/cache-split token usage
from their captured ``usage.json`` — unlike grok's single cumulative total,
codex's JSONL carries a genuine split (see ``codex_cli_usage``), so finalize
must return the real 4-tuple instead of folding everything into output.
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


def _write_usage(path: Path, **fields: object) -> None:
    payload = {
        "model": "gpt-5.3-codex",
        "tokens_input": 0,
        "tokens_output": 0,
        "tokens_cache_read": 0,
        "tokens_cache_write": 0,
        "cost_usd": 0.0,
        "turns": 1,
        **fields,
    }
    path.write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def test_codex_usage_returns_real_split(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    usage = tmp_path / "usage.json"
    _write_usage(
        usage, tokens_input=700, tokens_output=250, tokens_cache_read=300, turns=2
    )
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(
        orch, "_codex_usage_json", lambda _aid: json.loads(usage.read_text())
    )

    expected_turns = 2
    assert orch._codex_usage_tokens("be-dev-1") == (700, 250, 300, 0)
    assert orch._codex_usage_turns("be-dev-1") == expected_turns


def test_read_usage_json_contained_refuses_escape(tmp_path: Path) -> None:
    """A '..' id resolves outside the usage root and must be refused —
    basename alone does not neutralize '..', the containment check does."""
    (tmp_path / "usage.json").write_text('{"leak": 1}', encoding="utf-8")
    base = tmp_path / "root"
    base.mkdir()

    assert AgentOrchestrator._read_usage_json_contained(base, "..") is None


def test_read_usage_json_contained_reads_inside_root(tmp_path: Path) -> None:
    agent_dir = tmp_path / "be-dev-1"
    agent_dir.mkdir()
    (agent_dir / "usage.json").write_text('{"total_tokens": 5}', encoding="utf-8")

    data = AgentOrchestrator._read_usage_json_contained(tmp_path, "be-dev-1")
    assert data == {"total_tokens": 5}


def test_codex_usage_zero_when_store_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(orch, "_codex_usage_json", lambda _aid: None)
    assert orch._codex_usage_tokens("be-dev-1") == (0, 0, 0, 0)
    assert orch._codex_usage_turns("be-dev-1") == 0


@pytest.mark.asyncio
async def test_resolve_final_usage_routes_openai_to_usage_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(
        orch,
        "_codex_usage_json",
        lambda _aid: {
            "tokens_input": 12,
            "tokens_output": 34,
            "tokens_cache_read": 5,
            "tokens_cache_write": 1,
        },
    )
    cfg = type("C", (), {"provider_type": "openai"})()
    orch._instances = {"be-dev-1": AgentInstance(agent_id="be-dev-1", config=cfg)}

    assert await orch._resolve_final_token_usage("be-dev-1") == (12, 34, 5, 1)


@pytest.mark.asyncio
async def test_resolve_final_turns_tools_routes_openai_to_usage_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(orch, "_codex_usage_turns", lambda _aid: 3)
    cfg = type("C", (), {"provider_type": "openai"})()
    orch._instances = {"be-dev-1": AgentInstance(agent_id="be-dev-1", config=cfg)}

    # Codex has no tool-call signal — tool_calls stays 0.
    assert await orch._resolve_final_turns_tools("be-dev-1") == (3, 0)


@pytest.mark.asyncio
async def test_resolve_active_tokens_routes_openai_to_usage_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(
        orch,
        "_codex_usage_json",
        lambda _aid: {"tokens_input": 12, "tokens_output": 34},
    )
    cfg = type("C", (), {"provider_type": "openai"})()
    orch._instances = {"be-dev-1": AgentInstance(agent_id="be-dev-1", config=cfg)}
    async with httpx.AsyncClient() as client:
        assert await orch._resolve_active_tokens(client, "be-dev-1") == (12, 34, 0, 0)


def test_codex_usage_dir_branches_compose_vs_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(orch_mod, "PROJECT_HOST_PATH", "")
    local = AgentOrchestrator._codex_usage_dir("be-dev-1")
    assert "roboco-codex-usage" in str(local)
    assert local.name == "be-dev-1"

    monkeypatch.setattr(orch_mod, "PROJECT_HOST_PATH", "/volume1/roboco")
    monkeypatch.setattr(orch_mod, "CODEX_USAGE_DATA_DIR", "/data/codex-usage")
    assert str(AgentOrchestrator._codex_usage_dir("be-dev-1")) == (
        "/data/codex-usage/be-dev-1"
    )


@pytest.mark.parametrize(
    "bad",
    ["..", ".", "../etc", "a/b", "a\\b", "", "be-dev-1/../x", "x\x00y"],
)
def test_codex_usage_dir_rejects_path_traversal(bad: str) -> None:
    with pytest.raises(ValueError, match="unsafe agent id"):
        AgentOrchestrator._codex_usage_dir(bad)


def test_codex_usage_json_reads_the_real_local_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(orch_mod, "PROJECT_HOST_PATH", "")
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    udir = tmp_path / "roboco-codex-usage" / "be-dev-1"
    udir.mkdir(parents=True)
    _write_usage(udir / "usage.json", tokens_input=55, tokens_output=10)
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    assert orch._codex_usage_tokens("be-dev-1") == (55, 10, 0, 0)
