"""Descriptive-commit-message gate.

The gateway's ``commit()`` tool calls this validator before writing. CI also
runs the same validation as a backstop. Configurable via pyproject.toml
[tool.roboco.commits].
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Defaults; overridable via roboco.config.Settings (and pyproject [tool.roboco.commits])
DEFAULT_MIN_CHARS: int = 20
DEFAULT_BANNED_WORDS: tuple[str, ...] = (
    "wip",
    "tmp",
    "asdf",
    "oops",
    "fix",
    "update",
    "change",
    "stuff",
    "things",
)

_CONVENTIONAL_RE = re.compile(
    r"^(?P<type>feat|fix|chore|docs|refactor|test|perf|build|ci)"
    r"(?:\((?P<scope>[\w\-_/.]+)\))?"
    r":\s+(?P<subject>.+)$"
)

_CONVENTIONAL_HINT = (
    "consider Conventional Commits shape: "
    "<type>(<scope>): <subject>  "
    "(types: feat|fix|chore|docs|refactor|test|perf|build|ci)"
)


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reason: str | None = None
    hint: str | None = None
    remediate: str | None = None
    # B20 commit_intent (decisions pilot, default-off): advisory evidence
    # from the intent noul ("intent looks mismatched: re-read the diff").
    # NEVER affects ``ok``; absent (None) when the pilot is off / shadow /
    # below-floor / unreachable, i.e. exactly the pre-pilot result.
    evidence: dict[str, Any] | None = None


def validate_commit_message(
    message: str,
    *,
    min_chars: int = DEFAULT_MIN_CHARS,
    banned_words: tuple[str, ...] = DEFAULT_BANNED_WORDS,
) -> ValidationResult:
    """Validate a commit-message subject (first line, no [task-id] prefix)."""
    msg = message.strip()

    if not msg:
        return ValidationResult(
            ok=False,
            reason="empty message",
            remediate=_remediate(min_chars=min_chars, banned_words=banned_words),
        )

    # Length check first: short messages always fail regardless of word content.
    # This means single banned words like "wip" (3 chars) are caught here with
    # a "shorter than" reason, satisfying both the length and banned-word tests.
    if len(msg) < min_chars:
        return ValidationResult(
            ok=False,
            reason=f"shorter than {min_chars} chars",
            remediate=_remediate(min_chars=min_chars, banned_words=banned_words),
        )

    # Single-token banned-word check: catches longer banned words that somehow
    # meet the minimum length (unlikely with DEFAULT_MIN_CHARS=20 but enforced).
    if msg.lower() in banned_words:
        return ValidationResult(
            ok=False,
            reason=f"banned single-word message: {msg!r}",
            remediate=_remediate(min_chars=min_chars, banned_words=banned_words),
        )

    # Conventional shape — soft hint, not a rejection.
    if _CONVENTIONAL_RE.match(msg):
        return ValidationResult(ok=True)

    return ValidationResult(ok=True, hint=_CONVENTIONAL_HINT)


def _intent_diff_fallback(
    diff_summary: str | None, files_changed: list[str] | None
) -> str:
    """Compose a diff summary for the intent noul when the caller has no
    diff text: the touched-file list is the next-best intent signal."""
    if diff_summary and diff_summary.strip():
        return diff_summary
    if files_changed:
        return "touched files: " + ", ".join(str(f) for f in files_changed[:20])
    return ""


async def validate_commit_message_with_intent(
    session,
    message: str,
    *,
    task_id: str | None = None,
    diff_summary: str | None = None,
    files_changed: list[str] | None = None,
    min_chars: int = DEFAULT_MIN_CHARS,
    banned_words: tuple[str, ...] = DEFAULT_BANNED_WORDS,
) -> ValidationResult:
    """The B20 commit_intent lane (decisions pilot, default-off): the
    structural validator plus an ADVISORY intent hint.

    The shape/length/banned-word checks run first and stay authoritative:
    this function never bypasses and never strengthens them. When they
    pass AND the pilot is ON and confident the message does not match the
    diff's intent, the returned result carries
    ``evidence={"intent_hint": "intent looks mismatched: re-read the
    diff"}``. Fail-open: pilot off/shadow/below-floor/unreachable (or no
    diff signal at all) returns the structural result unchanged.
    """
    result = validate_commit_message(
        message, min_chars=min_chars, banned_words=banned_words
    )
    if not result.ok:
        return result
    summary = _intent_diff_fallback(diff_summary, files_changed)
    if not summary:
        return result
    try:
        from roboco.services.decisions.pilots_gateway import commit_intent_hint

        hint = await commit_intent_hint(
            session,
            task_id=task_id or "",
            message=message,
            diff_summary=summary,
        )
    except Exception as exc:
        logger.warning(
            "commit_intent_skip",
            task_id=task_id,
            error=str(exc),
        )
        return result
    if hint:
        return replace(result, evidence={"intent_hint": hint})
    return result


def _remediate(
    *,
    min_chars: int,
    banned_words: tuple[str, ...],
) -> str:
    banned = ", ".join(banned_words)
    return (
        "rewrite the commit subject as: <type>(<scope>): <what changed and why>. "
        f"min length: {min_chars} chars. "
        f"banned single-word patterns: {banned}."
    )
