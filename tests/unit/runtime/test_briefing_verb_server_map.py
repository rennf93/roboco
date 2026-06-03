"""The session briefing must carry a verb->MCP-server map + key preconditions.

Agents repeatedly fumble their first move: calling raw bash/http/shell-git,
invoking ``evidence`` on roboco-flow (it lives on roboco-do), omitting the
``nature`` argument on ``delegate``, or skipping the required journal note
before claiming. The role docs cover this but agents cannot read them at
spawn, so the briefing embeds a concise, role-accurate block generated from
the role's actual manifest (``get_role_config``).
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import patch

from roboco.runtime.orchestrator import AgentOrchestrator
from roboco.services.gateway.role_config import get_role_config


def _orch() -> AgentOrchestrator:
    with patch.object(AgentOrchestrator, "__init__", return_value=None):
        orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._VERB_SERVER_CACHE = {}
    return orch


def test_developer_block_maps_flow_verbs_to_flow_server() -> None:
    block = _orch()._build_verb_server_block("developer")
    cfg = get_role_config("developer")
    # Every flow verb the role can call is attributed to roboco-flow.
    flow_section = block.split("roboco-flow", 1)[1].split("roboco-do", 1)[0]
    for verb in cfg.flow_tools:
        assert verb in flow_section, f"{verb} missing from roboco-flow line"


def test_developer_block_puts_evidence_on_do_not_flow() -> None:
    block = _orch()._build_verb_server_block("developer")
    do_section = block.split("roboco-do", 1)[1].split("roboco-git-readonly", 1)[0]
    flow_section = block.split("roboco-flow", 1)[1].split("roboco-do", 1)[0]
    assert "evidence" in do_section
    assert "evidence" not in flow_section


def test_developer_block_lists_git_readonly_and_optimal_servers() -> None:
    block = _orch()._build_verb_server_block("developer")
    assert "roboco-git-readonly" in block
    assert "roboco-optimal" in block
    assert "roboco_ask_mentor" in block


def test_developer_block_states_note_before_claim_precondition() -> None:
    block = _orch()._build_verb_server_block("developer")
    assert "note(scope='decision')" in block
    assert "i_will_work_on" in block.split("note(scope='decision')", 1)[1]


def test_developer_block_forbids_raw_bash_http_shell_git() -> None:
    block = _orch()._build_verb_server_block("developer")
    lowered = block.lower()
    assert "shell git" in lowered or "shell-git" in lowered
    assert "raw" in lowered


def test_pm_block_states_delegate_requires_nature() -> None:
    block = _orch()._build_verb_server_block("cell_pm")
    assert "delegate" in block
    assert "nature" in block


def test_qa_block_has_no_delegate_or_note_before_claim_noise() -> None:
    # QA has no delegate verb, so the nature precondition must not appear;
    # QA has no claim-with-plan verb, so note-before-claim must not appear.
    block = _orch()._build_verb_server_block("qa")
    assert "delegate" not in block
    assert "note(scope='decision')" not in block
    # But it must still carry the no-raw-bash rule and its own flow verbs.
    assert "pass_review" in block
    assert "shell" in block.lower()


def test_unknown_role_returns_empty() -> None:
    assert _orch()._build_verb_server_block("nonexistent") == ""


def test_block_is_cached_per_role() -> None:
    orch = _orch()
    first = orch._build_verb_server_block("developer")
    second = orch._build_verb_server_block("developer")
    assert first is second


def test_block_is_embedded_in_written_briefing() -> None:
    orch = _orch()
    orch._VERB_SERVER_CACHE = {}
    orch._TOOL_LOAD_CACHE = {}

    with (
        patch("roboco.runtime.orchestrator.get_agent_role", return_value="developer"),
        patch("roboco.runtime.orchestrator.get_agent_team", return_value="backend"),
        patch(
            "roboco.runtime.orchestrator.get_escalation_target", return_value="be-pm"
        ),
        patch("roboco.runtime.orchestrator.PROJECT_HOST_PATH", None),
        patch(
            "roboco.runtime.orchestrator.tempfile.gettempdir",
            return_value=tempfile.gettempdir(),
        ),
    ):
        path = asyncio.run(
            orch._write_agent_briefing("be-dev-1", None, "/data/workspaces/x")
        )

    assert path is not None
    content = Path(path).read_text()
    assert "roboco-flow" in content
    assert "roboco-do" in content
    assert "note(scope='decision')" in content
