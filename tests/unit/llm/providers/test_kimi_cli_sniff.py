"""kimi_cli_sniff — classify a Kimi run from ONLY its machine-relevant text.

The structural guarantee under test: the model's own on-topic prose (which
can legitimately contain the words "quota-limited" or a "429"/"401" substring
inside a commit hash / id) must NEVER reach the classifier, because
extraction only pulls a structured ``error`` field off error-bearing JSONL
events plus raw stderr — never ``role: assistant`` / ``role: tool`` content.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from roboco.llm.providers import kimi_cli_sniff as sniff

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _write_jsonl(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _error_event(message: str) -> str:
    return json.dumps({"type": "error", "error": {"message": message}})


# ---------------------------------------------------------------------------
# extract_error_text — structural isolation
# ---------------------------------------------------------------------------


def test_extract_error_text_pulls_only_structured_error_field(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(
        log,
        [
            json.dumps(
                {
                    "role": "assistant",
                    "content": "the quota-limited rollout ships this sprint",
                }
            ),
            _error_event("real error text"),
        ],
    )
    assert sniff.extract_error_text(log) == "real error text"


def test_extract_error_text_accepts_bare_string_error() -> None:
    assert sniff._error_text_from_event({"error": "bare string error"}) == (
        "bare string error"
    )


def test_extract_error_text_empty_for_missing_or_error_less_log(
    tmp_path: Path,
) -> None:
    assert sniff.extract_error_text(tmp_path / "nope.jsonl") == ""
    log = tmp_path / "run.jsonl"
    _write_jsonl(log, [json.dumps({"role": "assistant", "content": "hi"})])
    assert sniff.extract_error_text(log) == ""


# ---------------------------------------------------------------------------
# The false-positive class this module exists to kill
# ---------------------------------------------------------------------------


def test_benign_transcript_never_false_parks(tmp_path: Path) -> None:
    """A transcript whose ONLY content is benign on-topic prose — mentioning
    "quota-limited" work and a commit hash containing "429"/"401" — must
    classify as "" (no park), because none of it lives in a structured error
    field the extractor even looks at."""
    log = tmp_path / "run.jsonl"
    _write_jsonl(
        log,
        [
            json.dumps(
                {
                    "role": "assistant",
                    "content": (
                        "Fixed the quota-limited rollout gate. Committed as "
                        "abc4291f, also touched item 40199."
                    ),
                }
            ),
            json.dumps({"role": "tool", "tool_call_id": "1", "content": "ok"}),
        ],
    )
    err_log = tmp_path / "run.err"
    err_log.write_text("", encoding="utf-8")
    assert sniff.classify(log, err_log) == ""


def test_word_boundary_prevents_429_substring_false_positive() -> None:
    assert not sniff.is_rate_limited("commit abc14293 deployed to prod")
    assert not sniff.is_rate_limited("fix4297abc landed")


def test_word_boundary_prevents_401_substring_false_positive() -> None:
    assert not sniff.is_auth_failure("item 40199 was resolved")
    assert not sniff.is_auth_failure("ticket 14012 closed")


# ---------------------------------------------------------------------------
# True positives — the live-verified error text shapes from the spike
# ---------------------------------------------------------------------------


def test_status_code_429_classifies_rate_limit(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(log, [_error_event("request failed with status code: 429")])
    assert sniff.classify(log) == "rate_limit"


def test_engine_overloaded_classifies_rate_limit(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(log, [_error_event("the engine is currently overloaded")])
    assert sniff.classify(log) == "rate_limit"


def test_usage_limit_for_period_classifies_rate_limit(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(log, [_error_event("usage limit for this period exceeded")])
    assert sniff.classify(log) == "rate_limit"


def test_usage_limit_for_billing_cycle_classifies_rate_limit(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(log, [_error_event("usage limit for this billing cycle reached")])
    assert sniff.classify(log) == "rate_limit"


def test_api_key_invalid_classifies_auth(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(log, [_error_event("API Key appears to be invalid")])
    assert sniff.classify(log) == "auth"


def test_membership_benefits_classifies_auth(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(
        log,
        [_error_event("We're unable to verify your membership benefits at this time.")],
    )
    assert sniff.classify(log) == "auth"


def test_classify_reads_stderr_too(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(log, [json.dumps({"role": "assistant", "content": "ok"})])
    err_log = tmp_path / "run.err"
    err_log.write_text("fatal: status code: 429\n", encoding="utf-8")
    assert sniff.classify(log, err_log) == "rate_limit"


def test_classify_missing_files_returns_empty(tmp_path: Path) -> None:
    assert sniff.classify(tmp_path / "nope.jsonl", tmp_path / "nope.err") == ""


def test_rate_limit_checked_before_auth_when_both_present(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(
        log,
        [_error_event("status code: 429, and API Key appears to be invalid too")],
    )
    assert sniff.classify(log) == "rate_limit"


def test_main_cli_prints_classification(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(log, [_error_event("status code: 429")])
    assert sniff.main([str(log)]) == 0
    assert capsys.readouterr().out.strip() == "rate_limit"


def test_main_cli_no_args_prints_empty(capsys: pytest.CaptureFixture[str]) -> None:
    assert sniff.main([]) == 0
    assert capsys.readouterr().out.strip() == ""
