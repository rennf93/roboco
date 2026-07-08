"""project_slug defaults to ROBOCO_PROJECT_SLUG when omitted on every
roboco-git-readonly tool — agents no longer need to guess a slug, and the
RAG-taught literal "roboco" example (a non-existent slug) can't 404 them.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

if TYPE_CHECKING:
    import types


@pytest.fixture
def git_module(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    monkeypatch.setenv("ROBOCO_AGENT_ID", "00000000-0000-0000-0000-000000000042")
    monkeypatch.setenv("ROBOCO_AGENT_ROLE", "developer")
    monkeypatch.setenv("ROBOCO_ORCHESTRATOR_URL", "http://test-orchestrator:8000")
    monkeypatch.delenv("ROBOCO_PROJECT_SLUG", raising=False)
    import roboco.mcp.git_readonly as srv

    importlib.reload(srv)
    return srv


def test_resolve_omitted_falls_back_to_env(
    git_module: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ROBOCO_PROJECT_SLUG", "roboco-api")
    assert git_module._resolve_project_slug(None) == "roboco-api"
    assert git_module._resolve_project_slug("") == "roboco-api"


def test_resolve_supplied_passes_through(
    git_module: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ROBOCO_PROJECT_SLUG", "roboco-api")
    assert git_module._resolve_project_slug("roboco-panel") == "roboco-panel"


def test_resolve_neither_present_returns_clear_error(
    git_module: types.ModuleType,
) -> None:
    result = git_module._resolve_project_slug(None)
    assert isinstance(result, dict)
    assert result["error"] == "missing_project_slug"
    assert "ROBOCO_PROJECT_SLUG" in result["detail"]


def _mock_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return resp


def test_git_status_omitted_slug_uses_env(
    git_module: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ROBOCO_PROJECT_SLUG", "roboco-api")
    with patch("httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.get.return_value = _mock_response({"branch": "main"})
        git_module.roboco_git_status()
        _, kwargs = client.get.call_args
        assert kwargs["params"]["project_slug"] == "roboco-api"


def test_git_status_missing_both_returns_error_without_http_call(
    git_module: types.ModuleType,
) -> None:
    with patch("httpx.Client") as client_cls:
        result = git_module.roboco_git_status()
        client_cls.assert_not_called()
    assert result["error"] == "missing_project_slug"
