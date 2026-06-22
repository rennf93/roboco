"""Hygiene checks: inline comments and lint/type suppressions.

Comment nodes come from the AST (so markers inside strings are never matched).
An *inline* comment trails code on its line; a full-line comment (indented or
not) is allowed. Suppression markers are language-scoped.
"""

from __future__ import annotations

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
    if any(marker in text for marker in _SUPPRESSIONS.get(language, ())):
        out.append(_finding(rel_path, row + 1, "no_lint_suppressions", standard))
    return out


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
