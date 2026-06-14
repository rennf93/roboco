"""Tests for descriptive-commit-message gate."""

from __future__ import annotations

import pytest
from roboco.services.gateway.commit_validator import (
    DEFAULT_MIN_CHARS,
    ValidationResult,
    validate_commit_message,
)


class TestSubjectLength:
    def test_short_message_rejected(self) -> None:
        r: ValidationResult = validate_commit_message("wip")
        assert r.ok is False
        assert r.reason is not None
        reason = r.reason.lower()
        assert "shorter than" in reason or "too short" in reason

    def test_exact_min_length_accepted(self) -> None:
        msg = "fix the auth header injection bug"  # >=DEFAULT_MIN_CHARS chars
        assert len(msg) >= DEFAULT_MIN_CHARS
        r: ValidationResult = validate_commit_message(msg)
        assert r.ok is True


class TestBannedWords:
    @pytest.mark.parametrize("word", ["wip", "tmp", "asdf", "oops", "fix", "update"])
    def test_single_banned_word_rejected(self, word: str) -> None:
        r: ValidationResult = validate_commit_message(word)
        assert r.ok is False

    def test_banned_word_in_long_message_accepted(self) -> None:
        # Banned-word check is only on single-token messages
        r: ValidationResult = validate_commit_message(
            "fix: handle null user id in auth middleware"
        )
        assert r.ok is True


class TestConventionalShape:
    def test_conventional_shape_accepted(self) -> None:
        r: ValidationResult = validate_commit_message(
            "feat(gateway): add claimant_lock for single-active-agent invariant"
        )
        assert r.ok is True

    def test_non_conventional_long_descriptive_accepted_with_hint(self) -> None:
        r: ValidationResult = validate_commit_message(
            "Refactored the workspace cloning logic to use the new path resolver"
        )
        assert r.ok is True
        # Soft hint, not a rejection
        assert r.hint is not None
        assert "conventional" in r.hint.lower()


class TestRemediate:
    def test_remediate_present_on_failure(self) -> None:
        r: ValidationResult = validate_commit_message("wip")
        assert r.remediate is not None
        assert "<type>" in r.remediate or "type" in r.remediate.lower()


def test_empty_message_rejected() -> None:
    """Lines 57-62: empty/whitespace-only message rejection branch."""
    r = validate_commit_message("   ")
    assert r.ok is False
    assert r.reason == "empty message"


def test_banned_word_long_enough_to_pass_min_chars() -> None:
    """Lines 76-81: single-token banned word that meets min_chars."""
    # Use min_chars=2 so 'wip' (3 chars) passes length but hits banned-word.
    r = validate_commit_message("wip", min_chars=2)
    assert r.ok is False
    assert r.reason is not None
    assert "banned single-word" in r.reason
