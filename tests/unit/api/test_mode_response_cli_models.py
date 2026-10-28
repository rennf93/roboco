"""ModeResponse (GET /api/providers) carries the effective CLI models."""

from __future__ import annotations

import pytest
from roboco.api.schemas.provider import ModeResponse
from roboco.config import settings
from roboco.models.llm_catalog import OLLAMA_DEFAULT_MODEL


def test_defaults_mirror_settings() -> None:
    resp = ModeResponse(mode="anthropic", assignments=[])
    assert resp.codex_cli_model == settings.codex_cli_model
    assert resp.gemini_cli_model == settings.gemini_cli_model
    assert resp.kimi_cli_model == settings.kimi_cli_model
    assert resp.ollama_default_model == OLLAMA_DEFAULT_MODEL


@pytest.mark.parametrize(
    "field", ["codex_cli_model", "gemini_cli_model", "kimi_cli_model"]
)
def test_override_changes_response(monkeypatch: pytest.MonkeyPatch, field: str) -> None:
    monkeypatch.setattr(settings, field, "custom/model-x")
    resp = ModeResponse(mode="anthropic", assignments=[])
    assert getattr(resp, field) == "custom/model-x"
