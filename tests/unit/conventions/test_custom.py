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


# ---------------------------------------------------------------------------
# .tsx is a TypeScript dialect: a rule scoped to ``typescript`` must fire on a
# ``.tsx`` file. The validator tags a .tsx file as language ``tsx`` (the JSX
# grammar needs that tag), so without dialect-awareness in the scoping check a
# ``languages: [typescript]`` rule — the language the scan *reports* for a
# React+TS repo — silently skipped every .tsx file.
# ---------------------------------------------------------------------------

_NO_CONSOLE = CustomRule(
    id="no-console",
    pattern=r"console\.",
    message="no console in app code",
    level="warn",
    languages=["typescript"],
)

_JSX_ONLY = CustomRule(
    id="jsx-only",
    pattern=r"jsx",
    message="jsx-specific",
    level="warn",
    languages=["tsx"],
)


def test_typescript_scoped_rule_fires_on_tsx_file() -> None:
    """A ``typescript``-scoped rule fires on a ``.tsx`` (language ``tsx``)
    file — tsx is a typescript dialect, and the scan reports the project as
    ``typescript`` so operators scope rules to that."""
    std = ConventionsStandard(custom=[_NO_CONSOLE])
    findings = check_custom("a.tsx", b"console.log(1)\n", "tsx", std)
    assert len(findings) == 1
    assert findings[0].rule == "no-console"


def test_typescript_scoped_rule_still_fires_on_ts_file() -> None:
    """Sanity: the typescript-scoped rule still fires on a plain ``.ts``
    (language ``typescript``) file — the dialect fix must not regress the
    direct-match case."""
    std = ConventionsStandard(custom=[_NO_CONSOLE])
    assert len(check_custom("a.ts", b"console.log(1)\n", "typescript", std)) == 1


def test_tsx_scoped_rule_does_not_fire_on_plain_typescript() -> None:
    """A ``tsx``-scoped rule (JSX-only) must NOT fire on a plain ``.ts``
    file — the dialect relation is one-directional (tsx ⊂ typescript), so a
    JSX-specific rule does not apply to non-JSX TypeScript."""
    std = ConventionsStandard(custom=[_JSX_ONLY])
    assert check_custom("a.ts", b"var jsx = 1\n", "typescript", std) == []


def test_tsx_scoped_rule_fires_on_tsx_file() -> None:
    """Sanity: a ``tsx``-scoped rule still fires on a ``.tsx`` file directly."""
    std = ConventionsStandard(custom=[_JSX_ONLY])
    assert len(check_custom("a.tsx", b"var jsx = 1\n", "tsx", std)) == 1
