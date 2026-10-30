"""Schema-model + YAML-parse tests for the architectural-conventions standard."""

from __future__ import annotations

import pytest
from roboco.foundation.policy.conventions.models import (
    BUILTIN_RULES,
    ConventionsParseError,
    ConventionsStandard,
    CustomRule,
    Module,
    Rule,
    Waiver,
)

_VALID_YAML = """
version: 1
languages: [python, typescript]
modules:
  - path: app/routers
    purpose: HTTP routes
    forbidden: [model, helper]
  - path: app/models
    purpose: Pydantic / ORM models
rules:
  no_models_in_routers: { level: block }
  no_inline_comments: { level: warn }
custom:
  - id: no-print
    pattern: '\\bprint\\('
    message: use the logger
    level: warn
    languages: [python]
waivers:
  - path: app/routers/legacy.py
    rule: no_models_in_routers
    reason: extraction tracked separately
"""


def test_valid_yaml_parses_to_standard() -> None:
    std = ConventionsStandard.parse_yaml(_VALID_YAML)
    assert std.version == 1
    assert std.languages == ["python", "typescript"]
    assert std.modules[0].path == "app/routers"
    assert std.modules[0].forbidden == ["model", "helper"]
    assert std.rules["no_models_in_routers"].level == "block"
    assert std.rules["no_models_in_routers"].name == "no_models_in_routers"
    assert std.custom[0].id == "no-print"
    assert std.custom[0].languages == ["python"]
    assert std.waivers[0].rule == "no_models_in_routers"


def test_empty_yaml_yields_default_standard() -> None:
    std = ConventionsStandard.parse_yaml("")
    assert std == ConventionsStandard()
    assert std.version == 1


def test_unknown_rule_level_raises_parse_error() -> None:
    with pytest.raises(ConventionsParseError):
        ConventionsStandard.parse_yaml(
            "rules:\n  no_models_in_routers: { level: explode }\n"
        )


def test_malformed_yaml_raises_parse_error() -> None:
    with pytest.raises(ConventionsParseError):
        ConventionsStandard.parse_yaml("modules: [unterminated\n")


def test_non_mapping_top_level_raises_parse_error() -> None:
    with pytest.raises(ConventionsParseError):
        ConventionsStandard.parse_yaml("- just\n- a\n- list\n")


def test_unknown_definition_kind_in_forbidden_raises() -> None:
    with pytest.raises(ConventionsParseError):
        ConventionsStandard.parse_yaml(
            "modules:\n  - path: x\n    purpose: y\n    forbidden: [wizard]\n"
        )


def test_builtin_rules_are_language_agnostic_hygiene_only() -> None:
    # BUILTIN_RULES are the universal hygiene defaults; placement / modularity
    # rules are derived per project from the scan, never seeded universally.
    assert BUILTIN_RULES["no_lint_suppressions"] == "block"
    assert BUILTIN_RULES["no_inline_comments"] == "warn"
    assert "no_models_in_routers" not in BUILTIN_RULES
    assert "no_helpers_in_routers" not in BUILTIN_RULES


def test_infra_section_parses_declared_globs() -> None:
    std = ConventionsStandard.parse_yaml('infra:\n  - "Dockerfile*"\n  - "docker/**"\n')
    assert std.infra == ["Dockerfile*", "docker/**"]


def test_infra_empty_list_is_explicit_optout_not_undeclared() -> None:
    std = ConventionsStandard.parse_yaml("infra: []\n")
    # [] and None are different declarations: [] opts out of the infra gate,
    # None falls back to the shipped defaults.
    assert std.infra == []


def test_infra_absent_is_none() -> None:
    std = ConventionsStandard.parse_yaml(_VALID_YAML)
    assert std.infra is None
    assert ConventionsStandard().infra is None


def test_unknown_top_level_key_is_ignored_forward_compatible() -> None:
    # extra="ignore": a new file read by an old binary must parse cleanly and
    # silently drop the unknown section (and vice versa for infra itself).
    std = ConventionsStandard.parse_yaml("some_future_section:\n  a: 1\n")
    assert std.infra is None


def test_models_construct_directly() -> None:
    mod = Module(path="app/services", purpose="logic", forbidden=["route"])
    assert mod.forbidden == ["route"]
    rule = Rule(name="no_print", level="warn")
    assert rule.level == "warn"
    custom = CustomRule(id="x", pattern="y", message="z", level="block")
    assert custom.languages == []
    waiver = Waiver(path="a.py", rule="no_models_in_routers", reason="r")
    assert waiver.path == "a.py"
