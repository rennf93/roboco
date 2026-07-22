"""codex_cli_sniff — classify a Codex run from ONLY its machine-relevant text.

The structural guarantee under test: the model's own on-topic prose (which
can legitimately contain the words "quota-limited", "login page", or a "429"
substring inside a commit hash / id) must NEVER reach the classifier, because
extraction only pulls ``error.message`` fields off error-bearing JSONL events
plus raw stderr — never ``turn.completed`` / ``item.*`` content.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from roboco.llm.providers import codex_cli_sniff as sniff

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _write_jsonl(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _turn_failed(message: str) -> str:
    return json.dumps({"type": "turn.failed", "error": {"message": message}})


# ---------------------------------------------------------------------------
# extract_error_text — structural isolation
# ---------------------------------------------------------------------------


def test_extract_error_text_pulls_only_error_message(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(
        log,
        [
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 1},
                    "text": "the quota-limited rollout ships this sprint",
                }
            ),
            _turn_failed("real error text"),
        ],
    )
    assert sniff.extract_error_text(log) == "real error text"


def test_extract_error_text_empty_for_missing_or_error_less_log(
    tmp_path: Path,
) -> None:
    assert sniff.extract_error_text(tmp_path / "nope.jsonl") == ""
    log = tmp_path / "run.jsonl"
    _write_jsonl(log, [json.dumps({"type": "turn.completed", "usage": {}})])
    assert sniff.extract_error_text(log) == ""


# ---------------------------------------------------------------------------
# The false-positive class this fix exists to kill
# ---------------------------------------------------------------------------


def test_benign_transcript_never_false_parks(tmp_path: Path) -> None:
    """A transcript whose ONLY content is benign on-topic prose — mentioning
    "quota-limited" work, a "login page" bug, and a commit hash containing
    "429" — must classify as "" (no park), because none of it lives in an
    error field the extractor even looks at."""
    log = tmp_path / "run.jsonl"
    _write_jsonl(
        log,
        [
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": (
                            "Fixed the quota-limited rollout gate and the "
                            "login page redirect bug. Committed as abc4291f."
                        ),
                    },
                }
            ),
        ],
    )
    err_log = tmp_path / "run.err"
    err_log.write_text("", encoding="utf-8")
    assert sniff.classify(log, err_log) == ""


def test_word_boundary_prevents_429_substring_false_positive() -> None:
    # "429" embedded inside a larger digit/word run must not match — grok's
    # own \b429\b pattern, restored here after an initial cut dropped it.
    assert not sniff.is_rate_limited("commit abc14293 deployed to prod")
    assert not sniff.is_rate_limited("fix4297abc landed")
    # "quota" alone legitimately matches wherever it appears (grok's own
    # pattern, unchanged) — the false-positive class this fix kills is SCOPE
    # (which text gets scanned, i.e. never turn.completed/item.* content),
    # not the word "quota" itself. See test_benign_transcript_never_false_parks.


def test_bare_login_word_does_not_classify_as_auth() -> None:
    # "login" was dropped from the auth pattern — a mention of a login PAGE
    # (this repo's own panel) must not false-park the provider.
    assert not sniff.is_auth_failure("please visit the login page to continue")
    assert not sniff.is_auth_failure("login required")


# ---------------------------------------------------------------------------
# True positives — real machine-extracted error text
# ---------------------------------------------------------------------------


def test_real_429_error_message_classifies_rate_limit(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(log, [_turn_failed("Rate limit exceeded: 429 Too Many Requests")])
    assert sniff.classify(log) == "rate_limit"


def test_insufficient_quota_error_message_classifies_rate_limit(
    tmp_path: Path,
) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(log, [_turn_failed("insufficient_quota: billing hard limit hit")])
    assert sniff.classify(log) == "rate_limit"


def test_exact_auth_phrase_refresh_token_expired_classifies_auth(
    tmp_path: Path,
) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(
        log, [_turn_failed("Your refresh token has expired, please re-authenticate")]
    )
    assert sniff.classify(log) == "auth"


def test_exact_auth_phrase_not_signed_in_classifies_auth(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(log, [_turn_failed("Error: not signed in")])
    assert sniff.classify(log) == "auth"


def test_classify_reads_stderr_too(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(log, [json.dumps({"type": "turn.completed", "usage": {}})])
    err_log = tmp_path / "run.err"
    err_log.write_text("fatal: 429 too many requests\n", encoding="utf-8")
    assert sniff.classify(log, err_log) == "rate_limit"


def test_classify_missing_files_returns_empty(tmp_path: Path) -> None:
    assert sniff.classify(tmp_path / "nope.jsonl", tmp_path / "nope.err") == ""


def test_rate_limit_checked_before_auth_when_both_present(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(
        log,
        [_turn_failed("429 too many requests, and also not signed in downstream")],
    )
    assert sniff.classify(log) == "rate_limit"


def test_main_cli_prints_classification(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(log, [_turn_failed("429 too many requests")])
    assert sniff.main([str(log)]) == 0
    assert capsys.readouterr().out.strip() == "rate_limit"


def test_main_cli_no_args_prints_empty(capsys: pytest.CaptureFixture[str]) -> None:
    assert sniff.main([]) == 0
    assert capsys.readouterr().out.strip() == ""
