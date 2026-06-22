"""Custom regex rules, scoped by language."""

from __future__ import annotations

from roboco.conventions.custom import check_custom
from roboco.foundation.policy.conventions.models import ConventionsStandard, CustomRule

_NO_PRINT = CustomRule(
    id="no-print",
    pattern=r"\bprint\(",
    message="use the logger, not print()",
    level="warn",
    languages=["python"],
)


def test_custom_rule_matches_in_scoped_language() -> None:
    std = ConventionsStandard(custom=[_NO_PRINT])
    findings = check_custom("a.py", b"print('x')\n", "python", std)
    assert len(findings) == 1
    assert findings[0].rule == "no-print"
    assert findings[0].level == "warn"
    assert findings[0].message == "use the logger, not print()"


def test_custom_rule_skips_other_language() -> None:
    std = ConventionsStandard(custom=[_NO_PRINT])
    assert check_custom("a.ts", b"print('x')\n", "typescript", std) == []


def test_unscoped_custom_rule_applies_to_all_languages() -> None:
    rule = CustomRule(
        id="no-log", pattern=r"console\.log", message="no console.log", level="warn"
    )
    std = ConventionsStandard(custom=[rule])
    assert check_custom("a.ts", b"console.log(1)\n", "typescript", std)


def test_custom_rule_reports_correct_line() -> None:
    print_line = 3
    std = ConventionsStandard(custom=[_NO_PRINT])
    findings = check_custom("a.py", b"x = 1\ny = 2\nprint(x)\n", "python", std)
    assert findings[0].line == print_line


def test_bad_regex_abstains_without_crashing() -> None:
    rule = CustomRule(id="bad", pattern=r"(unclosed", message="m", level="block")
    std = ConventionsStandard(custom=[rule])
    assert check_custom("a.py", b"anything\n", "python", std) == []
