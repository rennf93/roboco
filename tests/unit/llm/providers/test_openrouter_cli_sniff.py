"""openrouter_cli_sniff — classify an opencode run from ONLY its machine-relevant
text.

The structural guarantee under test: the model's own on-topic prose (which
can legitimately contain the words "quota-limited" or a "429"/"401" substring
inside a commit hash / id) must NEVER reach the classifier, because extraction
only pulls a structured ``error`` field off ``type: "error"`` JSONL events plus
raw stderr — never ``type: "text"`` / ``type: "tool_call"`` content.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from roboco.llm.providers import openrouter_cli_sniff as sniff

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _write_jsonl(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _error_event(message: str, name: str | None = None) -> str:
    error: dict[str, object] = {"message": message}
    if name:
        error["name"] = name
    return json.dumps({"type": "error", "error": {"data": error}})


# ---------------------------------------------------------------------------
# _error_text_from_event — structural isolation
# ---------------------------------------------------------------------------


def test_error_text_from_event_pulls_data_message() -> None:
    event = json.loads(_error_event("real error text"))
    assert sniff._error_text_from_event(event) == "real error text"


def test_error_text_from_event_pulls_error_name() -> None:
    event = json.loads(_error_event("msg", name="RateLimitError"))
    text = sniff._error_text_from_event(event)
    assert text is not None
    assert "RateLimitError" in text
    assert "msg" in text


def test_error_text_from_event_returns_none_for_non_error() -> None:
    assert (
        sniff._error_text_from_event({"type": "text", "part": {"text": "hi"}}) is None
    )
    assert sniff._error_text_from_event({"type": "step_finish"}) is None


def test_error_text_from_event_accepts_bare_string_error() -> None:
    assert sniff._error_text_from_event({"error": "bare string"}) == "bare string"


def test_error_text_from_event_accepts_top_level_message() -> None:
    assert sniff._error_text_from_event({"type": "error", "message": "top msg"}) == (
        "top msg"
    )


# ---------------------------------------------------------------------------
# extract_error_text — the false-positive class this module exists to kill
# ---------------------------------------------------------------------------


def test_extract_error_text_pulls_only_structured_error_field(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(
        log,
        [
            json.dumps(
                {"type": "text", "part": {"text": "the quota-limited rollout ships"}}
            ),
            _error_event("real error text"),
        ],
    )
    assert sniff.extract_error_text(log) == "real error text"


def test_extract_error_text_empty_for_missing_log(tmp_path: Path) -> None:
    assert sniff.extract_error_text(tmp_path / "nope.jsonl") == ""


def test_extract_error_text_empty_for_prose_only_log(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(
        log,
        [json.dumps({"type": "text", "part": {"text": "hi"}})],
    )
    assert sniff.extract_error_text(log) == ""


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
                    "type": "text",
                    "part": {
                        "text": (
                            "Fixed the quota-limited rollout gate. Committed as "
                            "abc4291f, also touched item 40199."
                        )
                    },
                }
            ),
            json.dumps({"type": "tool_call", "tool": "bash", "content": "ok"}),
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
# True positives — the live-verified error text shapes
# ---------------------------------------------------------------------------


def test_status_code_429_classifies_rate_limit(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(log, [_error_event("request failed with status code: 429")])
    assert sniff.classify(log) == "rate_limit"


def test_rate_limit_error_name_classifies_rate_limit(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(log, [_error_event("exceeded", name="RateLimitError")])
    assert sniff.classify(log) == "rate_limit"


def test_overloaded_classifies_rate_limit(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(log, [_error_event("the engine is currently overloaded")])
    assert sniff.classify(log) == "rate_limit"


def test_quota_classifies_rate_limit(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(log, [_error_event("quota exceeded for this period")])
    assert sniff.classify(log) == "rate_limit"


def test_too_many_requests_classifies_rate_limit(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(log, [_error_event("too many requests")])
    assert sniff.classify(log) == "rate_limit"


def test_invalid_api_key_classifies_auth(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(log, [_error_event("invalid api key")])
    assert sniff.classify(log) == "auth"


def test_unauthorized_classifies_auth(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(log, [_error_event("unauthorized access")])
    assert sniff.classify(log) == "auth"


def test_status_code_401_classifies_auth(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(log, [_error_event("status code: 401")])
    assert sniff.classify(log) == "auth"


def test_authentication_error_name_classifies_auth(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(log, [_error_event("bad key", name="AuthenticationError")])
    assert sniff.classify(log) == "auth"


def test_classify_reads_stderr_too(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(log, [json.dumps({"type": "text", "part": {"text": "ok"}})])
    err_log = tmp_path / "run.err"
    err_log.write_text("fatal: status code: 429\n", encoding="utf-8")
    assert sniff.classify(log, err_log) == "rate_limit"


def test_classify_missing_files_returns_empty(tmp_path: Path) -> None:
    assert sniff.classify(tmp_path / "nope.jsonl", tmp_path / "nope.err") == ""


def test_rate_limit_checked_before_auth_when_both_present(tmp_path: Path) -> None:
    log = tmp_path / "run.jsonl"
    _write_jsonl(
        log,
        [_error_event("status code: 429, and invalid api key too")],
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
