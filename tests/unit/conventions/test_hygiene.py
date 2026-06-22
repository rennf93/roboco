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


def _src(line: str) -> bytes:
    # Build via a variable so a literal suppression marker never sits on this
    # test file's own source line (ruff would parse it as a real directive).
    return (line + "\n").encode()


def test_runtime_typing_noqa_is_allowed() -> None:
    # A runtime-needed typing import (pydantic / SQLAlchemy) — the sanctioned
    # escape, not error-silencing.
    findings = check_hygiene(
        "a.py", _src("from uuid import UUID  # noqa: TC003"), "python", _STD
    )
    assert _rules(findings, "no_lint_suppressions") == []


def test_pydantic_prop_decorator_ignore_is_allowed() -> None:
    findings = check_hygiene(
        "a.py", _src("y = f()  # type: ignore[prop-decorator]"), "python", _STD
    )
    assert _rules(findings, "no_lint_suppressions") == []


def test_other_ignore_code_is_still_flagged() -> None:
    findings = check_hygiene(
        "a.py", _src("x = bad()  # type: ignore[arg-type]"), "python", _STD
    )
    assert _rules(findings, "no_lint_suppressions")


def test_mixed_allowed_and_disallowed_codes_is_flagged() -> None:
    # One allowed code does not launder a disallowed one alongside it.
    findings = check_hygiene("a.py", _src("x = 1  # noqa: TC003, E501"), "python", _STD)
    assert _rules(findings, "no_lint_suppressions")


def test_rule_level_override_from_standard() -> None:
    std = ConventionsStandard(
        rules={"no_inline_comments": Rule(name="no_inline_comments", level="block")}
    )
    findings = check_hygiene("a.py", b"x = 1  # c\n", "python", std)
    assert _rules(findings, "no_inline_comments")[0].level == "block"
