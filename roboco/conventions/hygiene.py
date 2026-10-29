"""Hygiene checks: inline comments and lint/type suppressions.

Comment nodes come from the AST (so markers inside strings are never matched).
An *inline* comment trails code on its line; a full-line comment (indented or
not) is allowed. A suppression is a tool's directive where that tool reads it,
language-scoped; prose that only names a marker is not one.
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

# Suppression codes that are the SANCTIONED escape hatch rather than a silenced
# error: ruff's flake8-type-checking codes (TC001/2/3 — an import a framework
# needs at runtime, e.g. pydantic / SQLAlchemy / FastAPI, cannot move into a
# TYPE_CHECKING block) and pydantic's computed_field ``prop-decorator``. A
# suppression is allowed only when it carries codes AND every one is in this set;
# a bare ``noqa`` / ``type: ignore`` (blanket, no code) or any other code stays a
# finding. Keeps the rule's teeth on genuine error-silencing without footgunning
# the framework-mandated annotations every pydantic project needs.
_ALLOWED_SUPPRESSION_CODES = frozenset({"TC001", "TC002", "TC003", "prop-decorator"})
# A directive is the marker where its tool reads it: right after a ``#`` for
# ruff (noqa, any case), mypy (type: ignore) and bandit (nosec), and at the
# start of the comment for ESLint and TypeScript. A comment that merely names a
# marker ("we removed the noqa here") suppresses nothing and is no finding.
_NOQA_DIRECTIVE = re.compile(r"#\s*(?i:noqa)\b(?::\s*(?P<codes>[A-Z0-9, ]+))?")
_TYPE_IGNORE_DIRECTIVE = re.compile(r"#\s*type:\s*ignore\b(?:\[(?P<codes>[^\]]*)\])?")
_NOSEC_DIRECTIVE = re.compile(r"#\s*nosec\b")
_TS_DIRECTIVE = re.compile(
    r"^(?://|/\*+)\s*"
    r"(?:eslint-disable(?:-next-line|-line)?|@ts-(?:ignore|expect-error|nocheck))\b"
)
_SUPPRESSION_DIRECTIVES: dict[str, tuple[re.Pattern[str], ...]] = {
    "python": (_NOQA_DIRECTIVE, _TYPE_IGNORE_DIRECTIVE, _NOSEC_DIRECTIVE),
    "typescript": (_TS_DIRECTIVE,),
    "tsx": (_TS_DIRECTIVE,),
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
    directives = [
        match
        for pattern in _SUPPRESSION_DIRECTIVES.get(language, ())
        for match in pattern.finditer(text)
    ]
    if directives and not _suppression_allowed(directives):
        out.append(_finding(rel_path, row + 1, "no_lint_suppressions", standard))
    return out


def _suppression_allowed(directives: list[re.Match[str]]) -> bool:
    """True iff every directive lists only sanctioned framework-escape codes.

    A bare marker (no code) suppresses everything and is never allowed, nor
    is a directive with no code grammar here (nosec, ESLint, TypeScript).
    """
    codes: list[str] = []
    for directive in directives:
        group = directive.groupdict().get("codes")
        if not group:
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
