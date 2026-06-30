"""Custom-rule language scoping (#129 / #32).

A custom rule scoped to a language the validator never reports (a typo like
``pyhton``) silently never fires — the operator gets no signal that their
rule is dead. The fix surfaces each unrecognized tag as a ``warn`` finding on
the conventions file (visible in the findings feed + QA evidence, never
block-level so it cannot strand the gate). The ``tsx`` -> ``typescript``
dialect relation is intentional and stays (#32 BY-DESIGN): a ``typescript``
-rule must fire on a ``.tsx`` file.
"""

from __future__ import annotations

from roboco.conventions import runner
from roboco.conventions.custom import (
    _DIALECT_OF,
    _KNOWN_LANGUAGES,
    _rule_applies,
    unrecognized_rule_languages,
)
from roboco.conventions.runner import run
from roboco.foundation.policy.conventions.models import (
    ConventionsStandard,
    CustomRule,
)


def _rule(languages: list[str], *, rid: str = "r1") -> CustomRule:
    return CustomRule(
        id=rid,
        pattern="TODO",
        languages=languages,
        level="warn",
        message="m",
    )


def test_known_languages_cover_the_validator_tags() -> None:
    # The runner tags .py -> python, .ts -> typescript, .tsx -> tsx.
    assert frozenset({"python", "typescript", "tsx"}) == _KNOWN_LANGUAGES


def test_unrecognized_rule_languages_flags_a_typo() -> None:
    assert unrecognized_rule_languages(_rule(["pyhton"])) == ["pyhton"]


def test_unrecognized_rule_languages_clean_for_known_and_unscoped() -> None:
    assert unrecognized_rule_languages(_rule(["python"])) == []
    assert unrecognized_rule_languages(_rule(["typescript", "tsx"])) == []
    # An unscoped rule applies to everything — never unrecognized.
    assert unrecognized_rule_languages(_rule([])) == []


def test_run_emits_warn_finding_for_unknown_language_tag() -> None:
    standard = ConventionsStandard(custom=[_rule(["pyhton"], rid="no-todos")])
    findings = run(".", [], standard)
    scope = [f for f in findings if f.rule == "custom_language_scope"]
    assert len(scope) == 1
    assert scope[0].level == "warn"
    assert "no-todos" in scope[0].message
    assert "pyhton" in scope[0].message
    assert scope[0].file == ".roboco/conventions.yml"


def test_run_clean_when_custom_rules_use_known_languages() -> None:
    standard = ConventionsStandard(
        custom=[_rule(["typescript"], rid="r"), _rule([], rid="unscoped")]
    )
    findings = run(".", [], standard)
    assert not [f for f in findings if f.rule == "custom_language_scope"]


def test_tsx_is_a_typescript_dialect_by_design() -> None:
    """#32: the one-directional tsx -> typescript relation is intentional."""
    assert _DIALECT_OF == {"tsx": "typescript"}
    # A typescript-scoped rule applies to a tsx file; a tsx-only rule does not
    # apply to plain typescript.
    assert _rule_applies(["typescript"], "tsx") is True
    assert _rule_applies(["tsx"], "typescript") is False


def test_runner_module_exposes_run() -> None:
    # Sanity: the once-per-run validation hook lives on ``run``.
    assert callable(runner.run)
