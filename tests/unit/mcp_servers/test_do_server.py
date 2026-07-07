"""Tests for roboco-do MCP server."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Same pattern as test_flow_server: do_server now refuses to start without
# a manifest. The test fixture writes a stub manifest
# with the full do-tool superset; production manifests are role-scoped.
_DO_TEST_MANIFEST = {
    "agent_id": "00000000-0000-0000-0000-000000000001",
    "role": "developer",
    "team": "backend",
    "workspace_path": "/tmp/test",
    "flow_tools": [],
    "do_tools": ["commit", "note", "dm", "notify", "evidence"],
    "read_tools": [],
    "write_tools": [],
    "bash_allowed": True,
    "subagent_allowed": False,
    "subagent_model": None,
    "env": {},
}


@pytest.fixture
def do_module(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("ROBOCO_AGENT_ID", "00000000-0000-0000-0000-000000000001")
    monkeypatch.setenv("ROBOCO_AGENT_ROLE", "developer")
    monkeypatch.setenv("ROBOCO_ORCHESTRATOR_URL", "http://test-orchestrator:8000")
    manifest_path = Path(tempfile.mkdtemp()) / "tool-manifest.json"
    manifest_path.write_text(json.dumps(_DO_TEST_MANIFEST))
    monkeypatch.setenv("ROBOCO_TOOL_MANIFEST_PATH", str(manifest_path))
    import importlib

    import roboco.mcp.do_server as srv

    importlib.reload(srv)
    return srv


def test_commit_posts_message_and_files(do_module: Any) -> None:
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_response = MagicMock()
    fake_response.json.return_value = {"status": "in_progress", "task_id": "x"}
    fake_client.post.return_value = fake_response

    with patch("httpx.Client", return_value=fake_client):
        result = do_module.commit("feat(api): add /healthz", files=["foo.py"])

    assert result["status"] == "in_progress"
    args, kwargs = fake_client.post.call_args
    assert "/api/v1/do/commit" in args[0]
    assert kwargs["json"] == {"message": "feat(api): add /healthz", "files": ["foo.py"]}


def test_note_default_scope_note(do_module: Any) -> None:
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_response = MagicMock()
    fake_response.json.return_value = {"status": "noted"}
    fake_client.post.return_value = fake_response

    with patch("httpx.Client", return_value=fake_client):
        do_module.note("hello world")

    _args, kwargs = fake_client.post.call_args
    assert kwargs["json"]["scope"] == "note"


def test_note_with_scope_reflect(do_module: Any) -> None:
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_response = MagicMock()
    fake_response.json.return_value = {"status": "noted"}
    fake_client.post.return_value = fake_response

    with patch("httpx.Client", return_value=fake_client):
        do_module.note("did x", scope="reflect")

    _args, kwargs = fake_client.post.call_args
    assert kwargs["json"]["scope"] == "reflect"


def test_build_headers_carries_auth_token_and_team(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """do verbs must carry X-Agent-Token + X-Agent-Team or the API's
    ROBOCO_AGENT_AUTH_REQUIRED gate 401s with "Missing X-Agent-Token" —
    regression: the manual header dict omitted both, latent until auth was
    armed on the NAS deploy."""
    import importlib

    be_dev_1 = "00000000-0000-0000-0001-000000000001"  # role=developer, team=backend
    manifest = Path(tempfile.mkdtemp()) / "tool-manifest.json"
    manifest.write_text(json.dumps({**_DO_TEST_MANIFEST, "agent_id": be_dev_1}))
    monkeypatch.setenv("ROBOCO_AGENT_ID", be_dev_1)
    monkeypatch.setenv("ROBOCO_AGENT_ROLE", "developer")
    monkeypatch.setenv("ROBOCO_AGENT_TOKEN", "test-hmac-token")
    monkeypatch.setenv("ROBOCO_ORCHESTRATOR_URL", "http://test-orchestrator:8000")
    monkeypatch.setenv("ROBOCO_TOOL_MANIFEST_PATH", str(manifest))

    import roboco.mcp.do_server as srv

    importlib.reload(srv)
    headers = srv._build_headers()

    assert headers["X-Agent-ID"] == be_dev_1
    assert headers["X-Agent-Role"] == "developer"
    assert headers["X-Agent-Team"] == "backend"
    assert headers["X-Agent-Token"] == "test-hmac-token"
    assert "X-Correlation-ID" in headers


def test_build_headers_omits_unsigned_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """The orchestrator injects ROBOCO_AGENT_TOKEN=UNSIGNED when the HMAC
    secret is unset at spawn. The middleware rejects a presented-but-unverifiable
    token with 401 "signature mismatch" even in dev mode, so forwarding UNSIGNED
    turns every do verb into a 401. Omit the header so dev (auth not required)
    accepts the call; prod 401s with "Missing X-Agent-Token" instead."""
    import importlib

    be_dev_1 = "00000000-0000-0000-0001-000000000001"
    manifest = Path(tempfile.mkdtemp()) / "tool-manifest.json"
    manifest.write_text(json.dumps({**_DO_TEST_MANIFEST, "agent_id": be_dev_1}))
    monkeypatch.setenv("ROBOCO_AGENT_ID", be_dev_1)
    monkeypatch.setenv("ROBOCO_AGENT_ROLE", "developer")
    monkeypatch.setenv("ROBOCO_AGENT_TOKEN", "UNSIGNED")
    monkeypatch.setenv("ROBOCO_ORCHESTRATOR_URL", "http://test-orchestrator:8000")
    monkeypatch.setenv("ROBOCO_TOOL_MANIFEST_PATH", str(manifest))

    import roboco.mcp.do_server as srv

    importlib.reload(srv)
    headers = srv._build_headers()

    assert "X-Agent-Token" not in headers


def test_dm_posts_all_fields(do_module: Any) -> None:
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_response = MagicMock()
    fake_response.json.return_value = {"status": "sent"}
    fake_client.post.return_value = fake_response

    with patch("httpx.Client", return_value=fake_client):
        do_module.dm("be-qa", "review please", task_id="t1", skill="code_review")

    _args, kwargs = fake_client.post.call_args
    assert kwargs["json"] == {
        "recipient": "be-qa",
        "text": "review please",
        "task_id": "t1",
        "skill": "code_review",
    }


def test_evidence_posts_task_id(do_module: Any) -> None:
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_response = MagicMock()
    fake_response.json.return_value = {"status": "ok", "evidence": {}}
    fake_client.post.return_value = fake_response

    with patch("httpx.Client", return_value=fake_client):
        do_module.evidence("task-uuid")

    args, kwargs = fake_client.post.call_args
    assert "/api/v1/do/evidence" in args[0]
    assert kwargs["json"] == {"task_id": "task-uuid"}
