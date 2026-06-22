"""Effective-map merge tests: auto-derived defaults overlaid by the file."""

from __future__ import annotations

from roboco.foundation.policy.conventions.effective_map import effective_map
from roboco.foundation.policy.conventions.models import (
    ConventionsStandard,
    CustomRule,
    Module,
    Rule,
    Waiver,
)


def test_effective_map_applies_builtin_rules_when_file_absent() -> None:
    eff = effective_map(ConventionsStandard(), None)
    assert eff.rules["no_lint_suppressions"].level == "block"
    assert eff.rules["no_inline_comments"].level == "warn"
    # Placement / modularity rules are derived per project, not universal.
    assert "no_models_in_routers" not in eff.rules


def test_file_module_overrides_derived_by_path() -> None:
    derived = ConventionsStandard(
        modules=[Module(path="app/routers", purpose="routes")]
    )
    file = ConventionsStandard(
        modules=[Module(path="app/routers", purpose="routes", forbidden=["model"])]
    )
    eff = effective_map(derived, file)
    assert len(eff.modules) == 1
    assert eff.modules[0].forbidden == ["model"]


def test_file_module_appends_new_path() -> None:
    derived = ConventionsStandard(
        modules=[Module(path="app/routers", purpose="routes")]
    )
    file = ConventionsStandard(modules=[Module(path="app/models", purpose="models")])
    eff = effective_map(derived, file)
    assert [m.path for m in eff.modules] == ["app/routers", "app/models"]


def test_file_rule_overrides_builtin_level() -> None:
    file = ConventionsStandard(
        rules={"no_inline_comments": Rule(name="no_inline_comments", level="block")}
    )
    eff = effective_map(ConventionsStandard(), file)
    assert eff.rules["no_inline_comments"].level == "block"


def test_derived_rule_overrides_builtin_then_file_overrides_derived() -> None:
    derived = ConventionsStandard(
        rules={"no_inline_comments": Rule(name="no_inline_comments", level="block")}
    )
    eff_no_file = effective_map(derived, None)
    assert eff_no_file.rules["no_inline_comments"].level == "block"
    file = ConventionsStandard(
        rules={"no_inline_comments": Rule(name="no_inline_comments", level="warn")}
    )
    eff = effective_map(derived, file)
    assert eff.rules["no_inline_comments"].level == "warn"


def test_languages_are_unioned() -> None:
    derived = ConventionsStandard(languages=["python"])
    file = ConventionsStandard(languages=["python", "typescript"])
    eff = effective_map(derived, file)
    assert eff.languages == ["python", "typescript"]


def test_file_custom_and_waivers_replace_derived() -> None:
    derived = ConventionsStandard(
        custom=[CustomRule(id="d", pattern="d", message="d", level="warn")],
        waivers=[Waiver(path="d.py", rule="no_models_in_routers", reason="d")],
    )
    file = ConventionsStandard(
        custom=[CustomRule(id="f", pattern="f", message="f", level="block")],
        waivers=[Waiver(path="f.py", rule="no_helpers_in_routers", reason="f")],
    )
    eff = effective_map(derived, file)
    assert [c.id for c in eff.custom] == ["f"]
    assert [w.path for w in eff.waivers] == ["f.py"]


def test_file_none_keeps_derived_custom_and_waivers() -> None:
    derived = ConventionsStandard(
        custom=[CustomRule(id="d", pattern="d", message="d", level="warn")],
    )
    eff = effective_map(derived, None)
    assert [c.id for c in eff.custom] == ["d"]
