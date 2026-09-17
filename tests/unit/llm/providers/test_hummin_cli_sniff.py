"""Unit tests for roboco.llm.providers.hummin_cli_sniff."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from roboco.llm.providers import hummin_cli_sniff

if TYPE_CHECKING:
    from pathlib import Path


def _error_end(message: str) -> str:
    """The verified pi-ai error channel: an assistant message with
    stopReason "error" + errorMessage, wrapped in a message_end event."""
    return json.dumps(
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [],
                "stopReason": "error",
                "errorMessage": message,
            },
        }
    )


def test_rate_limit_marker_in_error_message(tmp_path: Path) -> None:
    run_log = tmp_path / "run.jsonl"
    run_log.write_text(
        _error_end("Z.ai: 429 too many requests") + "\n", encoding="utf-8"
    )
    assert hummin_cli_sniff.classify(run_log) == "rate_limit"


def test_auth_marker_in_error_message(tmp_path: Path) -> None:
    run_log = tmp_path / "run.jsonl"
    run_log.write_text(
        _error_end("Invalid API key provided (401)") + "\n", encoding="utf-8"
    )
    assert hummin_cli_sniff.classify(run_log) == "auth"


def test_stderr_only_auth_failure(tmp_path: Path) -> None:
    run_log = tmp_path / "run.jsonl"
    run_log.write_text("", encoding="utf-8")
    err_log = tmp_path / "run.err"
    err_log.write_text("zai: 403 forbidden for this api key\n", encoding="utf-8")
    assert hummin_cli_sniff.classify(run_log, err_log) == "auth"


def test_exit_zero_with_error_event_classifies(tmp_path: Path) -> None:
    # The JSON-mode trap: hummin exits 0 even when the assistant errored —
    # the classifier must still see the event-channel failure.
    run_log = tmp_path / "run.jsonl"
    run_log.write_text(
        _error_end("rate limit exceeded for the current quota") + "\n",
        encoding="utf-8",
    )
    assert hummin_cli_sniff.classify(run_log) == "rate_limit"


def test_assistant_prose_never_reaches_the_classifier(tmp_path: Path) -> None:
    # The model's OWN text blocks mentioning 429/quota/auth on-topic must
    # NOT trigger a classification — only the structured error channels are
    # read.
    run_log = tmp_path / "run.jsonl"
    run_log.write_text(
        json.dumps(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "stopReason": "stop",
                    "content": [
                        {
                            "type": "text",
                            "text": "The API returned a 429 once; I added a "
                            "retry with quota backoff and an auth check.",
                        }
                    ],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert hummin_cli_sniff.classify(run_log) == ""


def test_aborted_stop_reason_surfaces_as_error_text(tmp_path: Path) -> None:
    run_log = tmp_path / "run.jsonl"
    run_log.write_text(
        json.dumps(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [],
                    "stopReason": "aborted",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    # An abort without an errorMessage is extracted ("assistant stopped:
    # aborted") but matches neither park class.
    assert hummin_cli_sniff.classify(run_log) == ""
    text = hummin_cli_sniff.extract_error_text(run_log)
    assert "aborted" in text


def test_rate_limit_wins_over_auth(tmp_path: Path) -> None:
    run_log = tmp_path / "run.jsonl"
    run_log.write_text(
        "\n".join(
            [
                _error_end("401 unauthorized"),
                _error_end("429 rate limit"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    assert hummin_cli_sniff.classify(run_log) == "rate_limit"


def test_clean_run_classifies_empty(tmp_path: Path) -> None:
    run_log = tmp_path / "run.jsonl"
    run_log.write_text(
        json.dumps({"type": "session", "version": 3, "id": "s1"}) + "\n",
        encoding="utf-8",
    )
    assert hummin_cli_sniff.classify(run_log) == ""
