"""logging.py coverage — secret redaction + processors + setup helpers."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import structlog
from roboco.logging import (
    LogContext,
    _redact_secrets,
    _resolve_log_dir,
    add_app_context,
    get_logger,
    log_operation,
    redact_event_dict,
    setup_logging,
)

if TYPE_CHECKING:
    import pytest


def test_redact_secrets_passes_through_non_strings() -> None:
    _INT = 42
    assert _redact_secrets(_INT) == _INT
    assert _redact_secrets(None) is None
    assert _redact_secrets([1, 2]) == [1, 2]


def test_redact_secrets_redacts_classic_pat() -> None:
    out = _redact_secrets("token=ghp_abcdefghijklmnopqrstuvwxyz12345")
    assert "ghp_" not in out
    assert "<REDACTED>" in out


def test_redact_secrets_redacts_fine_grained_pat() -> None:
    out = _redact_secrets("token=github_pat_abcdefghijklmnopqrstuvwxyz12345")
    assert "github_pat_" not in out
    assert "<REDACTED>" in out


def test_redact_secrets_redacts_server_token() -> None:
    out = _redact_secrets("token=ghs_abcdefghijklmnopqrstuvwxyz12345")
    assert "ghs_" not in out


def test_redact_secrets_redacts_bearer_token() -> None:
    out = _redact_secrets("Authorization: bearer abcdef123456789012345")
    assert "<REDACTED>" in out


def test_redact_secrets_redacts_user_pass_url() -> None:
    out = _redact_secrets("https://user:supersecretpassword@github.com/x/y")
    assert "supersecretpassword" not in out


def test_redact_secrets_no_change_for_clean_string() -> None:
    out = _redact_secrets("just a normal log message")
    assert out == "just a normal log message"


def test_add_app_context_injects_app_name() -> None:
    event = {"event": "test"}
    out = add_app_context(None, "info", event)
    assert out["app"] == "roboco"
    assert "version" in out
    assert "environment" in out


def test_redact_event_dict_redacts_values() -> None:
    event = {
        "event": "test",
        "token": "ghp_abcdefghijklmnopqrstuvwxyz12345",
        "safe": "ok",
    }
    out = redact_event_dict(None, "info", event)
    assert "ghp_" not in out["token"]
    assert out["safe"] == "ok"


def test_redact_event_dict_preserves_non_string_values() -> None:
    _INT = 42
    event = {"event": "test", "count": _INT, "items": [1, 2, 3]}
    out = redact_event_dict(None, "info", event)
    assert out["count"] == _INT
    assert out["items"] == [1, 2, 3]


# ---------------------------------------------------------------------------
# setup_logging — covers both branches (development and production).
# ---------------------------------------------------------------------------


def test_setup_logging_development_branch(tmp_path: Path) -> None:
    """Development env uses the colored console renderer."""
    with (
        patch("roboco.logging.settings") as mock_settings,
        patch("roboco.logging._resolve_log_dir", return_value=tmp_path),
    ):
        mock_settings.environment = "development"
        mock_settings.app_version = "0.0.0"
        mock_settings.debug = True
        setup_logging()
    # After setup, the root logger has at least the stdout handler attached.
    assert len(logging.getLogger().handlers) >= 1


def test_setup_logging_production_branch(tmp_path: Path) -> None:
    """Production env uses JSON renderer."""
    with (
        patch("roboco.logging.settings") as mock_settings,
        patch("roboco.logging._resolve_log_dir", return_value=tmp_path),
    ):
        mock_settings.environment = "production"
        mock_settings.app_version = "1.0.0"
        mock_settings.debug = False
        setup_logging()
    assert len(logging.getLogger().handlers) >= 1


def test_setup_logging_handles_missing_log_dir() -> None:
    """If `log_dir` resolves to None, only stdout handler is attached."""
    with (
        patch("roboco.logging.settings") as mock_settings,
        patch("roboco.logging._resolve_log_dir", return_value=None),
    ):
        mock_settings.environment = "production"
        mock_settings.app_version = "x"
        mock_settings.debug = False
        setup_logging()
    handlers = logging.getLogger().handlers
    # No file handler should be created.
    has_file_handler = any(
        isinstance(h, logging.handlers.RotatingFileHandler) for h in handlers
    )
    assert has_file_handler is False


def test_setup_logging_handles_log_dir_oserror() -> None:
    """If mkdir raises OSError, the log_dir branch logs to stderr but doesn't crash."""

    class _BadPath:
        def mkdir(self, *_args: object, **_kwargs: object) -> None:
            raise OSError("permission denied")

        def __truediv__(self, _name: str) -> str:
            return "x"

    with (
        patch("roboco.logging.settings") as mock_settings,
        patch("roboco.logging._resolve_log_dir", return_value=_BadPath()),
    ):
        mock_settings.environment = "production"
        mock_settings.app_version = "x"
        mock_settings.debug = False
        # Should not raise.
        setup_logging()


# ---------------------------------------------------------------------------
# _resolve_log_dir — env override + container path + dev fallback.
# ---------------------------------------------------------------------------


def test_resolve_log_dir_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    custom_dir = "/tmp/custom-logs"
    monkeypatch.setenv("ROBOCO_LOG_DIR", custom_dir)
    out = _resolve_log_dir()
    assert out == Path(custom_dir)


def test_resolve_log_dir_container_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """When /data/logs exists, prefer it."""
    monkeypatch.delenv("ROBOCO_LOG_DIR", raising=False)
    with patch("roboco.logging.Path") as mock_path_cls:
        # Make Path("/data/logs").is_dir() return True
        container = mock_path_cls.return_value
        container.is_dir.return_value = True
        out = _resolve_log_dir()
    # container path is returned
    assert out is container


class _NonexistentPath:
    """Stand-in for Path that pretends nothing exists on disk."""

    def is_dir(self) -> bool:
        return False

    @property
    def parent(self) -> _NonexistentPath:
        return _NonexistentPath()


def test_resolve_log_dir_dev_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """When neither override nor container path exist, fall back to ./logs."""
    monkeypatch.delenv("ROBOCO_LOG_DIR", raising=False)

    real_path = Path

    def fake_path(p: str) -> object:
        if str(p) == "/data/logs":
            return _NonexistentPath()
        return real_path(p)

    with patch("roboco.logging.Path", side_effect=fake_path):
        out = _resolve_log_dir()
    assert isinstance(out, Path)
    assert str(out) == "logs"


# ---------------------------------------------------------------------------
# get_logger / LogContext / log_operation
# ---------------------------------------------------------------------------


def test_get_logger_returns_bound_logger() -> None:
    log = get_logger("tests.logging")
    assert hasattr(log, "info")


def test_get_logger_no_name_uses_default() -> None:
    log = get_logger()
    assert hasattr(log, "info")


def test_log_context_binds_and_unbinds() -> None:
    """LogContext.__enter__ binds, __exit__ unbinds."""
    with LogContext(custom_key="value") as ctx:
        bound = structlog.contextvars.get_contextvars()
        assert bound.get("custom_key") == "value"
        assert ctx.context == {"custom_key": "value"}
    after = structlog.contextvars.get_contextvars()
    assert "custom_key" not in after


def test_log_operation_with_resource() -> None:
    out = log_operation("create", resource_type="task", resource_id="t1")
    assert out["operation"] == "create"
    assert out["resource_type"] == "task"
    assert out["resource_id"] == "t1"


def test_log_operation_without_resource() -> None:
    out = log_operation("startup")
    assert out["operation"] == "startup"
    assert "resource_type" not in out


def test_log_operation_with_extra() -> None:
    out = log_operation("x", extra1="a", extra2="b")
    assert out["extra1"] == "a"
    assert out["extra2"] == "b"
