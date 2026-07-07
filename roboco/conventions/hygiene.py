"""Hygiene checks: inline comments and lint/type suppressions.

Comment nodes come from the AST (so markers inside strings are never matched).
An *inline* comment trails code on its line; a full-line comment (indented or
not) is allowed. Suppression markers are language-scoped.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from roboco.foundation.policy.conventions.models import (
    BUILTIN_RULES,
    ConventionsStandard,
)

from .findings import Finding
from .grammars import get_parser

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tree_sitter import Node

_SUPPRESSIONS: dict[str, tuple[str, ...]] = {
    "python": ("noqa", "type: ignore"),
    "typescript": ("eslint-disable", "ts-ignore", "ts-expect-error"),
    "tsx": ("eslint-disable", "ts-ignore", "ts-expect-error"),
}

# Suppression codes that are the SANCTIONED escape hatch rather than a silenced
# error: ruff's flake8-type-checking codes (TC001/2/3 — an import a framework
# needs at runtime, e.g. pydantic / SQLAlchemy / FastAPI, cannot move into a
# TYPE_CHECKING block) and pydantic's computed_field ``prop-decorator``. A
# suppression is allowed only when it carries codes AND every one is in this set;
# a bare ``noqa`` / ``type: ignore`` (blanket, no code) or any other code stays a
# finding. Keeps the rule's teeth on genuine error-silencing without footgunning
# the framework-mandated annotations every pydantic project needs.
_ALLOWED_SUPPRESSION_CODES = frozenset({"TC001", "TC002", "TC003", "prop-decorator"})
_NOQA_CODES = re.compile(r"noqa(?::\s*(?P<codes>[A-Z0-9, ]+))?")
_TYPE_IGNORE_CODES = re.compile(r"type:\s*ignore(?:\[(?P<codes>[^\]]*)\])?")
_SUPPRESSION_CODE_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "python": (_NOQA_CODES, _TYPE_IGNORE_CODES),
}
_HYGIENE_TEXT: dict[str, tuple[str, str]] = {
    "no_inline_comments": (
        "inline comment trailing code — keep narration out of the code",
        "remove the trailing comment or lift it into a docstring",
    ),
    "no_lint_suppressions": (
        "lint/type suppression used — fix the root cause instead",
        "remove the suppression and resolve the underlying error",
    ),
}


def check_hygiene(
    rel_path: str, source: bytes, language: str, standard: ConventionsStandard
) -> list[Finding]:
    """Return inline-comment + suppression findings for ``source``."""
    root = get_parser(language).parse(source).root_node
    lines = source.split(b"\n")
    findings: list[Finding] = []
    for comment in _iter_comments(root):
        findings.extend(_comment_findings(rel_path, comment, lines, language, standard))
    return findings


def _comment_findings(
    rel_path: str,
    comment: Node,
    lines: list[bytes],
    language: str,
    standard: ConventionsStandard,
) -> list[Finding]:
    row, col = comment.start_point
    out: list[Finding] = []
    if _is_inline(lines, row, col):
        out.append(_finding(rel_path, row + 1, "no_inline_comments", standard))
    text = comment.text.decode(errors="replace") if comment.text else ""
    if any(
        marker in text for marker in _SUPPRESSIONS.get(language, ())
    ) and not _suppression_allowed(text, language):
        out.append(_finding(rel_path, row + 1, "no_lint_suppressions", standard))
    return out


def _suppression_allowed(text: str, language: str) -> bool:
    """True iff this suppression lists only sanctioned framework-escape codes.

    A bare marker (no code) suppresses everything and is never allowed; a
    language with no code grammar here (e.g. TypeScript) is never allowed.
    """
    codes: list[str] = []
    for pattern in _SUPPRESSION_CODE_PATTERNS.get(language, ()):
        for match in pattern.finditer(text):
            group = match.group("codes")
            if group is None:
                return False
            codes += [c.strip() for c in re.split(r"[,\s]+", group) if c.strip()]
    return bool(codes) and all(c in _ALLOWED_SUPPRESSION_CODES for c in codes)


def _is_inline(lines: list[bytes], row: int, col: int) -> bool:
    if row >= len(lines):
        return False
    return bool(lines[row][:col].strip())


def _finding(
    rel_path: str, line: int, rule: str, standard: ConventionsStandard
) -> Finding:
    message, fix_hint = _HYGIENE_TEXT[rule]
    return Finding(
        file=rel_path,
        line=line,
        kind=None,
        rule=rule,
        level=_rule_level(standard, rule),
        message=message,
        fix_hint=fix_hint,
    )


def _rule_level(standard: ConventionsStandard, name: str) -> str:
    rule = standard.rules.get(name)
    if rule is not None:
        return rule.level
    return BUILTIN_RULES.get(name, "block")


def _iter_comments(root: Node) -> Iterator[Node]:
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "comment":
            yield node
        stack.extend(node.children)
