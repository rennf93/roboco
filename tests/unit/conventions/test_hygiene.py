"""Hygiene checks: inline comments + lint/type suppressions."""

from __future__ import annotations

from roboco.conventions.hygiene import check_hygiene
from roboco.foundation.policy.conventions.models import ConventionsStandard, Rule

_STD = ConventionsStandard()


def _rules(findings: list, rule: str) -> list:
    return [f for f in findings if f.rule == rule]


def test_trailing_comment_is_flagged_inline() -> None:
    findings = check_hygiene("a.py", b"x = 1  # set x\n", "python", _STD)
    inline = _rules(findings, "no_inline_comments")
    assert inline and inline[0].level == "warn"
    assert inline[0].line == 1


def test_full_line_comment_is_not_inline() -> None:
    findings = check_hygiene("a.py", b"# a heading\nx = 1\n", "python", _STD)
    assert _rules(findings, "no_inline_comments") == []


def test_indented_full_line_comment_is_not_inline() -> None:
    src = b"def f():\n    # explain\n    return 1\n"
    findings = check_hygiene("a.py", src, "python", _STD)
    assert _rules(findings, "no_inline_comments") == []


def test_python_type_ignore_flags_suppression_block() -> None:
    findings = check_hygiene("a.py", b"y = bad()  # type: ignore\n", "python", _STD)
    sup = _rules(findings, "no_lint_suppressions")
    assert sup and sup[0].level == "block"


def test_python_noqa_flags_suppression() -> None:
    findings = check_hygiene("a.py", b"import os  # noqa: F401\n", "python", _STD)
    assert _rules(findings, "no_lint_suppressions")


def test_ts_eslint_disable_flags_suppression() -> None:
    src = b"// eslint-disable-next-line\nconst x = 1;\n"
    findings = check_hygiene("a.ts", src, "typescript", _STD)
    assert _rules(findings, "no_lint_suppressions")


def test_ts_ignore_flags_suppression() -> None:
    src = b"// @ts-ignore\nconst x: number = 'no';\n"
    findings = check_hygiene("a.ts", src, "typescript", _STD)
    assert _rules(findings, "no_lint_suppressions")


def test_python_marker_not_applied_to_typescript() -> None:
    src = b"// noqa is a python thing\nconst x = 1;\n"
    findings = check_hygiene("a.ts", src, "typescript", _STD)
    assert _rules(findings, "no_lint_suppressions") == []


def test_rule_level_override_from_standard() -> None:
    std = ConventionsStandard(
        rules={"no_inline_comments": Rule(name="no_inline_comments", level="block")}
    )
    findings = check_hygiene("a.py", b"x = 1  # c\n", "python", std)
    assert _rules(findings, "no_inline_comments")[0].level == "block"
