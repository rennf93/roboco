"""Descriptive-commit-message gate.

The gateway's ``commit()`` tool calls this validator before writing. CI also
runs the same validation as a backstop. Configurable via pyproject.toml
[tool.roboco.commits].
"""

from __future__ import annotations

import re
from dataclasses import dataclass

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
