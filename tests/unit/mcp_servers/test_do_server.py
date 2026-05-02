"""Tests for roboco-do MCP server."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def do_module(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ROBOCO_AGENT_ID", "00000000-0000-0000-0000-000000000001")
    monkeypatch.setenv("ROBOCO_AGENT_ROLE", "developer")
    monkeypatch.setenv("ROBOCO_ORCHESTRATOR_URL", "http://test-orchestrator:8000")
    import importlib

    import roboco.mcp.do_server as srv

    importlib.reload(srv)
    return srv


def test_commit_posts_message_and_files(do_module):  # type: ignore[no-untyped-def]
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_response = MagicMock()
    fake_response.json.return_value = {"status": "in_progress", "task_id": "x"}
    fake_client.post.return_value = fake_response

    with patch("httpx.Client", return_value=fake_client):
        result = do_module.commit("feat(api): add /healthz", files=["foo.py"])

    assert result["status"] == "in_progress"
    args, kwargs = fake_client.post.call_args
    assert "/api/v2/do/commit" in args[0]
    assert kwargs["json"] == {"message": "feat(api): add /healthz", "files": ["foo.py"]}


def test_note_default_scope_note(do_module):  # type: ignore[no-untyped-def]
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_response = MagicMock()
    fake_response.json.return_value = {"status": "noted"}
    fake_client.post.return_value = fake_response

    with patch("httpx.Client", return_value=fake_client):
        do_module.note("hello world")

    _args, kwargs = fake_client.post.call_args
    assert kwargs["json"]["scope"] == "note"


def test_note_with_scope_reflect(do_module):  # type: ignore[no-untyped-def]
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_response = MagicMock()
    fake_response.json.return_value = {"status": "noted"}
    fake_client.post.return_value = fake_response

    with patch("httpx.Client", return_value=fake_client):
        do_module.note("did x", scope="reflect")

    _args, kwargs = fake_client.post.call_args
    assert kwargs["json"]["scope"] == "reflect"


def test_say_posts_channel_and_text(do_module):  # type: ignore[no-untyped-def]
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_response = MagicMock()
    fake_response.json.return_value = {"status": "posted"}
    fake_client.post.return_value = fake_response

    with patch("httpx.Client", return_value=fake_client):
        do_module.say("backend-cell", "hello")

    _args, kwargs = fake_client.post.call_args
    assert kwargs["json"] == {
        "channel": "backend-cell",
        "text": "hello",
        "task_id": None,
    }


def test_dm_posts_all_fields(do_module):  # type: ignore[no-untyped-def]
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


def test_evidence_posts_task_id(do_module):  # type: ignore[no-untyped-def]
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_response = MagicMock()
    fake_response.json.return_value = {"status": "ok", "evidence": {}}
    fake_client.post.return_value = fake_response

    with patch("httpx.Client", return_value=fake_client):
        do_module.evidence("task-uuid")

    args, kwargs = fake_client.post.call_args
    assert "/api/v2/do/evidence" in args[0]
    assert kwargs["json"] == {"task_id": "task-uuid"}
