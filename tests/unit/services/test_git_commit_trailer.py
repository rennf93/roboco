"""Unit tests: commit-trailer links use ROBOCO_PUBLIC_BASE_URL."""

import importlib

import pytest
import roboco.config as config_module
from roboco.templates.git.commit import CommitContext, build_commit_message


def _make_ctx() -> CommitContext:
    return CommitContext(
        task_id="t-1",
        root_task_id="r-1",
        agent_slug="be-dev-1",
        session_id="s-1",
        commit_type="feat",
        scope=None,
        description="add user authentication",
    )


def test_build_commit_message_uses_given_api_base() -> None:
    """build_commit_message embeds the api_base it receives."""
    ctx = _make_ctx()
    out = build_commit_message(ctx, "https://roboco.example.com/api")
    assert "https://roboco.example.com/api" in out
    assert "127.0.0.1" not in out


def test_build_commit_message_strips_trailing_slash() -> None:
    """Trailing slash on api_base is stripped to avoid double slashes."""
    ctx = _make_ctx()
    out = build_commit_message(ctx, "https://roboco.example.com/api/")
    assert "//tasks/" not in out
    assert "https://roboco.example.com/api/tasks/" in out


def test_links_use_public_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """settings.public_base_url drives the api_base passed to build_commit_message."""
    monkeypatch.setenv("ROBOCO_PUBLIC_BASE_URL", "https://roboco.example.com")
    importlib.reload(config_module)
    try:
        settings = config_module.Settings()
        api_base = settings.public_base_url.rstrip("/") + "/api"
        ctx = _make_ctx()
        out = build_commit_message(ctx, api_base)
        assert "https://roboco.example.com" in out
        assert "127.0.0.1" not in out
    finally:
        importlib.reload(config_module)
