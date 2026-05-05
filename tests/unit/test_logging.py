"""logging.py coverage — secret redaction + processors."""

from __future__ import annotations

from roboco.logging import (
    _redact_secrets,
    add_app_context,
    redact_event_dict,
)


def test_redact_secrets_passes_through_non_strings() -> None:
    assert _redact_secrets(42) == 42
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
    event = {"event": "test", "count": 42, "items": [1, 2, 3]}
    out = redact_event_dict(None, "info", event)
    assert out["count"] == 42
    assert out["items"] == [1, 2, 3]
