"""Tests for roboco-flow MCP server."""

from __future__ import annotations

import importlib
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

if TYPE_CHECKING:
    import types
    from pathlib import Path


_FULL_MANIFEST = {
    "agent_id": "00000000-0000-0000-0000-000000000001",
    "role": "developer",
    "team": "backend",
    "workspace_path": "/tmp/test",
    # Test fixture provides every flow verb so per-verb URL/path tests work
    # against a single fixture. Production manifests are role-scoped.
    "flow_tools": [
        "give_me_work",
        "i_will_work_on",
        "open_pr",
        "i_am_done",
        "i_am_blocked",
        "unclaim",
        "resume",
        "i_am_idle",
        "claim_review",
        "pass",
        "fail",
        "claim_doc_task",
        "i_documented",
        "triage",
        "triage_all",
        "unblock",
        "complete",
        "escalate_up",
        "i_will_plan",
        "delegate",
        "submit_up",
        "escalate_to_ceo",
    ],
    "do_tools": ["commit", "note", "say", "dm", "evidence"],
    "read_tools": ["Read", "Glob", "Grep"],
    "write_tools": ["Edit", "Write"],
    "bash_allowed": True,
    "subagent_allowed": False,
    "subagent_model": None,
    "env": {},
}


@pytest.fixture()
def flow_module(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> types.ModuleType:
    """Import the flow_server module with controlled env vars + manifest."""
    manifest_path = tmp_path / "tool-manifest.json"
    manifest_path.write_text(json.dumps(_FULL_MANIFEST))

    monkeypatch.setenv("ROBOCO_AGENT_ID", "00000000-0000-0000-0000-000000000001")
    monkeypatch.setenv("ROBOCO_AGENT_ROLE", "developer")
    monkeypatch.setenv("ROBOCO_ORCHESTRATOR_URL", "http://test-orchestrator:8000")
    monkeypatch.setenv("ROBOCO_TOOL_MANIFEST_PATH", str(manifest_path))

    import roboco.mcp.flow_server as srv

    importlib.reload(srv)
    return srv


def _make_fake_client(return_value: dict[str, Any]) -> MagicMock:
    """Build a fake httpx.Client context-manager that returns the given dict."""
    fake_response = MagicMock()
    fake_response.json.return_value = return_value
    fake_client = MagicMock()
    fake_client.__enter__ = MagicMock(return_value=fake_client)
    fake_client.__exit__ = MagicMock(return_value=False)
    fake_client.post.return_value = fake_response
    return fake_client


def _reload_for_role(
    monkeypatch: pytest.MonkeyPatch, role: str, agent_id: str
) -> types.ModuleType:
    """Set env + write manifest for the given role; reload flow_server.

    The manifest provides the full verb superset so role-specific tests
    aren't blocked by the manifest filter; the role-scoped URL routing
    is what's under test in these per-role cases.
    """
    import tempfile
    from pathlib import Path

    manifest_path = Path(tempfile.mkdtemp()) / "tool-manifest.json"
    payload = {**_FULL_MANIFEST, "role": role, "agent_id": agent_id}
    manifest_path.write_text(json.dumps(payload))

    monkeypatch.setenv("ROBOCO_AGENT_ID", agent_id)
    monkeypatch.setenv("ROBOCO_AGENT_ROLE", role)
    monkeypatch.setenv("ROBOCO_ORCHESTRATOR_URL", "http://test-orchestrator:8000")
    monkeypatch.setenv("ROBOCO_TOOL_MANIFEST_PATH", str(manifest_path))

    import roboco.mcp.flow_server as srv

    importlib.reload(srv)
    return srv


def test_role_path_uses_agent_role(flow_module: types.ModuleType) -> None:
    expected = "/api/v1/flow/developer/give_me_work"
    assert flow_module._role_path("give_me_work") == expected


def test_role_path_includes_verb(flow_module: types.ModuleType) -> None:
    assert flow_module._role_path("i_am_done") == "/api/v1/flow/developer/i_am_done"


def test_give_me_work_posts_to_orchestrator(flow_module: types.ModuleType) -> None:
    fake_client = _make_fake_client({"status": "idle", "task_id": None})

    with patch("httpx.Client", return_value=fake_client):
        result = flow_module.give_me_work()

    assert result == {"status": "idle", "task_id": None}
    fake_client.post.assert_called_once()
    args, kwargs = fake_client.post.call_args
    assert "/api/v1/flow/developer/give_me_work" in args[0]
    assert kwargs["headers"]["X-Agent-ID"] == "00000000-0000-0000-0000-000000000001"
    assert kwargs["headers"]["X-Agent-Role"] == "developer"


def test_i_will_work_on_passes_plan(flow_module: types.ModuleType) -> None:
    fake_client = _make_fake_client({"status": "in_progress"})

    with patch("httpx.Client", return_value=fake_client):
        flow_module.i_will_work_on("task-uuid", plan="my plan")

    args, kwargs = fake_client.post.call_args
    assert kwargs["json"] == {
        "task_id": "task-uuid",
        "plan": "my plan",
        "steps": [],
        "technical_considerations": [],
        "risks": [],
        "open_questions": [],
    }
    assert "/api/v1/flow/developer/i_will_work_on" in args[0]


def test_i_will_work_on_plan_defaults_to_none(flow_module: types.ModuleType) -> None:
    fake_client = _make_fake_client({"status": "in_progress"})

    with patch("httpx.Client", return_value=fake_client):
        flow_module.i_will_work_on("task-uuid")

    _, kwargs = fake_client.post.call_args
    assert kwargs["json"] == {
        "task_id": "task-uuid",
        "plan": None,
        "steps": [],
        "technical_considerations": [],
        "risks": [],
        "open_questions": [],
    }


def test_i_will_work_on_passes_steps(flow_module: types.ModuleType) -> None:
    """#172: the MCP tool must forward the steps checklist; the
    server-side _dev_steps_gate rejects a fresh claim without it, so a
    tool that drops steps wedges every code task."""
    fake_client = _make_fake_client({"status": "in_progress"})
    steps = [
        {"title": "Checkout", "description": "create branch and read README"},
        {"title": "Edit", "description": "append the timestamp comment"},
    ]

    with patch("httpx.Client", return_value=fake_client):
        flow_module.i_will_work_on("task-uuid", plan="p", steps=steps)

    _, kwargs = fake_client.post.call_args
    assert kwargs["json"] == {
        "task_id": "task-uuid",
        "plan": "p",
        "steps": steps,
        "technical_considerations": [],
        "risks": [],
        "open_questions": [],
    }


def test_i_am_done_sends_task_id_and_notes(flow_module: types.ModuleType) -> None:
    fake_client = _make_fake_client({"status": "awaiting_qa"})

    with patch("httpx.Client", return_value=fake_client):
        result = flow_module.i_am_done("task-abc", notes="all tests green")

    assert result == {"status": "awaiting_qa"}
    args, kwargs = fake_client.post.call_args
    assert "/api/v1/flow/developer/i_am_done" in args[0]
    assert kwargs["json"] == {"task_id": "task-abc", "notes": "all tests green"}


def test_i_am_done_notes_defaults_to_empty(flow_module: types.ModuleType) -> None:
    fake_client = _make_fake_client({"status": "awaiting_qa"})

    with patch("httpx.Client", return_value=fake_client):
        flow_module.i_am_done("task-abc")

    _, kwargs = fake_client.post.call_args
    assert kwargs["json"]["notes"] == ""


def test_i_am_blocked_sends_reason(flow_module: types.ModuleType) -> None:
    fake_client = _make_fake_client({"status": "blocked"})

    with patch("httpx.Client", return_value=fake_client):
        result = flow_module.i_am_blocked("task-xyz", reason="waiting for env var")

    assert result == {"status": "blocked"}
    _, kwargs = fake_client.post.call_args
    # Pre-gateway parity (G8): i_am_blocked now also carries optional
    # blocker_type / what_needed — when caller omits them the wrapper
    # forwards `None` so the backend can fall back to the default
    # 'internal' classification.
    assert kwargs["json"] == {
        "task_id": "task-xyz",
        "reason": "waiting for env var",
        "blocker_type": None,
        "what_needed": None,
    }


def test_i_am_idle_posts_empty_body(flow_module: types.ModuleType) -> None:
    fake_client = _make_fake_client({"status": "idle"})

    with patch("httpx.Client", return_value=fake_client):
        result = flow_module.i_am_idle()

    assert result == {"status": "idle"}
    _, kwargs = fake_client.post.call_args
    assert kwargs["json"] == {}
    assert "/api/v1/flow/developer/i_am_idle" in fake_client.post.call_args[0][0]


def test_claim_review_posts_to_qa_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """When AGENT_ROLE=qa, claim_review forwards to /api/v1/flow/qa/claim_review."""
    srv = _reload_for_role(monkeypatch, "qa", "00000000-0000-0000-0000-000000000002")

    fake_client = _make_fake_client({"status": "claimed", "evidence": {}})

    with patch("httpx.Client", return_value=fake_client):
        result = srv.claim_review("task-uuid")

    assert result["status"] == "claimed"
    args, kwargs = fake_client.post.call_args
    assert "/api/v1/flow/qa/claim_review" in args[0]
    assert kwargs["json"] == {"task_id": "task-uuid"}


def test_pass_review_passes_notes(monkeypatch: pytest.MonkeyPatch) -> None:
    srv = _reload_for_role(monkeypatch, "qa", "00000000-0000-0000-0000-000000000002")

    fake_client = _make_fake_client({"status": "awaiting_documentation"})

    notes = "x" * 100
    with patch("httpx.Client", return_value=fake_client):
        srv.pass_review("task-uuid", notes)

    args, kwargs = fake_client.post.call_args
    assert "/api/v1/flow/qa/pass" in args[0]
    assert kwargs["json"] == {"task_id": "task-uuid", "notes": notes}


def test_fail_review_passes_issues_list(monkeypatch: pytest.MonkeyPatch) -> None:
    srv = _reload_for_role(monkeypatch, "qa", "00000000-0000-0000-0000-000000000002")

    fake_client = _make_fake_client({"status": "needs_revision"})

    issues = ["Missing test for /healthz", "Lint errors"]
    with patch("httpx.Client", return_value=fake_client):
        srv.fail_review("task-uuid", issues)

    args, kwargs = fake_client.post.call_args
    assert "/api/v1/flow/qa/fail" in args[0]
    assert kwargs["json"] == {"task_id": "task-uuid", "issues": issues}


def test_claim_doc_task_posts_to_documenter_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When AGENT_ROLE=documenter, claim_doc_task forwards to documenter flow."""
    srv = _reload_for_role(
        monkeypatch, "documenter", "00000000-0000-0000-0000-000000000003"
    )

    fake_client = _make_fake_client({"status": "claimed"})

    with patch("httpx.Client", return_value=fake_client):
        result = srv.claim_doc_task("task-uuid")

    assert result["status"] == "claimed"
    args, kwargs = fake_client.post.call_args
    assert "/api/v1/flow/documenter/claim_doc_task" in args[0]
    assert kwargs["json"] == {"task_id": "task-uuid"}


def test_i_documented_passes_notes_and_files(monkeypatch: pytest.MonkeyPatch) -> None:
    srv = _reload_for_role(
        monkeypatch, "documenter", "00000000-0000-0000-0000-000000000003"
    )

    fake_client = _make_fake_client({"status": "awaiting_pm_review"})

    with patch("httpx.Client", return_value=fake_client):
        result = srv.i_documented("task-uuid", "wrote docs/foo.md", ["docs/foo.md"])

    assert result["status"] == "awaiting_pm_review"
    args, kwargs = fake_client.post.call_args
    assert "/api/v1/flow/documenter/i_documented" in args[0]
    assert kwargs["json"] == {
        "task_id": "task-uuid",
        "notes": "wrote docs/foo.md",
        "files": ["docs/foo.md"],
    }


def test_triage_uses_role_path(monkeypatch: pytest.MonkeyPatch) -> None:
    srv = _reload_for_role(
        monkeypatch, "cell_pm", "00000000-0000-0000-0000-000000000004"
    )

    fake_client = _make_fake_client({"status": "blocked"})

    with patch("httpx.Client", return_value=fake_client):
        result = srv.triage()

    assert result["status"] == "blocked"
    args, kwargs = fake_client.post.call_args
    assert "/api/v1/flow/cell_pm/triage" in args[0]
    assert kwargs["json"] == {}


def test_triage_all_uses_role_path(monkeypatch: pytest.MonkeyPatch) -> None:
    srv = _reload_for_role(
        monkeypatch, "main_pm", "00000000-0000-0000-0000-000000000005"
    )

    fake_client = _make_fake_client({"status": "idle"})

    with patch("httpx.Client", return_value=fake_client):
        result = srv.triage_all()

    assert result["status"] == "idle"
    args, kwargs = fake_client.post.call_args
    assert "/api/v1/flow/main_pm/triage_all" in args[0]
    assert kwargs["json"] == {}


def test_unblock_with_restore_true(monkeypatch: pytest.MonkeyPatch) -> None:
    srv = _reload_for_role(
        monkeypatch, "cell_pm", "00000000-0000-0000-0000-000000000004"
    )

    fake_client = _make_fake_client({"status": "in_progress"})

    with patch("httpx.Client", return_value=fake_client):
        result = srv.unblock("task-uuid", "block resolved upstream; restoring")

    assert result["status"] == "in_progress"
    args, kwargs = fake_client.post.call_args
    assert "/api/v1/flow/cell_pm/unblock" in args[0]
    assert kwargs["json"] == {
        "task_id": "task-uuid",
        "reason": "block resolved upstream; restoring",
        "restore": True,
    }


def test_unblock_with_restore_false(monkeypatch: pytest.MonkeyPatch) -> None:
    srv = _reload_for_role(
        monkeypatch, "cell_pm", "00000000-0000-0000-0000-000000000004"
    )

    fake_client = _make_fake_client({"status": "in_progress"})

    with patch("httpx.Client", return_value=fake_client):
        result = srv.unblock(
            "task-uuid", "block resolved upstream; restoring", restore=False
        )

    assert result["status"] == "in_progress"
    _args, kwargs = fake_client.post.call_args
    assert kwargs["json"] == {
        "task_id": "task-uuid",
        "reason": "block resolved upstream; restoring",
        "restore": False,
    }


def test_complete_passes_notes(monkeypatch: pytest.MonkeyPatch) -> None:
    srv = _reload_for_role(
        monkeypatch, "cell_pm", "00000000-0000-0000-0000-000000000004"
    )

    fake_client = _make_fake_client({"status": "completed"})

    with patch("httpx.Client", return_value=fake_client):
        result = srv.complete("task-uuid", notes="approved")

    assert result["status"] == "completed"
    args, kwargs = fake_client.post.call_args
    assert "/api/v1/flow/cell_pm/complete" in args[0]
    assert kwargs["json"] == {"task_id": "task-uuid", "notes": "approved"}


def test_escalate_up_passes_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    srv = _reload_for_role(
        monkeypatch, "cell_pm", "00000000-0000-0000-0000-000000000004"
    )

    fake_client = _make_fake_client({"status": "blocked"})

    with patch("httpx.Client", return_value=fake_client):
        result = srv.escalate_up("task-uuid", reason="cross-cell help needed")

    assert result["status"] == "blocked"
    args, kwargs = fake_client.post.call_args
    assert "/api/v1/flow/cell_pm/escalate_up" in args[0]
    assert kwargs["json"] == {
        "task_id": "task-uuid",
        "reason": "cross-cell help needed",
    }


def test_escalate_to_ceo_passes_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """Board / Main PM verb forwards to /api/v1/flow/<role>/escalate_to_ceo."""
    srv = _reload_for_role(
        monkeypatch, "product_owner", "00000000-0000-0000-0000-000000000005"
    )

    fake_client = _make_fake_client({"status": "awaiting_ceo_approval"})

    with patch("httpx.Client", return_value=fake_client):
        result = srv.escalate_to_ceo("task-uuid", reason="strategic decision needed")

    assert result["status"] == "awaiting_ceo_approval"
    args, kwargs = fake_client.post.call_args
    # Board route serves PO + Head Marketing under one prefix; the slug
    # map in flow_server translates product_owner → board.
    assert "/api/v1/flow/board/escalate_to_ceo" in args[0]
    assert kwargs["json"] == {
        "task_id": "task-uuid",
        "reason": "strategic decision needed",
    }
