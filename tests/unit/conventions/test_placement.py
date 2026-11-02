"""Placement checks: a def whose kind is forbidden in its module is flagged."""

from __future__ import annotations

import json

import pytest
from roboco.conventions.placement import (
    Definition,
    check_placement,
    is_non_source_path,
)
from roboco.foundation.policy.conventions.models import (
    ConventionsStandard,
    Module,
    Rule,
)

_MODEL_LINE = 2
_DEFS: list[Definition] = [("UserCreate", _MODEL_LINE, "model")]


def test_forbidden_kind_in_module_is_flagged() -> None:
    std = ConventionsStandard(
        modules=[Module(path="app/routers", purpose="routes", forbidden=["model"])]
    )
    findings = check_placement("app/routers/users.py", _DEFS, std)
    assert len(findings) == 1
    f = findings[0]
    assert f.kind == "model"
    assert f.rule == "no_models_in_routers"
    assert f.level == "block"
    assert f.line == _MODEL_LINE
    assert "app/routers" in f.message


def test_allowed_kind_in_module_is_not_flagged() -> None:
    std = ConventionsStandard(
        modules=[Module(path="app/models", purpose="models", forbidden=["route"])]
    )
    assert check_placement("app/models/user.py", _DEFS, std) == []


def test_no_matching_module_yields_no_finding() -> None:
    std = ConventionsStandard(
        modules=[Module(path="app/routers", purpose="routes", forbidden=["model"])]
    )
    assert check_placement("lib/helpers.py", _DEFS, std) == []


def test_rule_level_from_standard_is_respected() -> None:
    std = ConventionsStandard(
        modules=[Module(path="app/routers", purpose="routes", forbidden=["model"])],
        rules={"no_models_in_routers": Rule(name="no_models_in_routers", level="warn")},
    )
    findings = check_placement("app/routers/users.py", _DEFS, std)
    assert findings[0].level == "warn"


def test_longest_matching_module_wins() -> None:
    std = ConventionsStandard(
        modules=[
            Module(path="app", purpose="root", forbidden=[]),
            Module(path="app/routers", purpose="routes", forbidden=["model"]),
        ]
    )
    findings = check_placement("app/routers/users.py", _DEFS, std)
    assert len(findings) == 1
    assert findings[0].kind == "model"


def test_prefix_must_be_on_a_path_boundary() -> None:
    # "app/routers" must not match "app/routers_legacy/..." spuriously.
    std = ConventionsStandard(
        modules=[Module(path="app/routers", purpose="routes", forbidden=["model"])]
    )
    assert check_placement("app/routers_legacy/users.py", _DEFS, std) == []


def test_finding_serializes_to_json_line() -> None:
    std = ConventionsStandard(
        modules=[Module(path="app/routers", purpose="routes", forbidden=["model"])]
    )
    f = check_placement("app/routers/users.py", _DEFS, std)[0]
    payload = json.loads(f.as_json())
    assert payload["rule"] == "no_models_in_routers"
    assert payload["file"] == "app/routers/users.py"
    assert payload["line"] == _MODEL_LINE
    assert payload["level"] == "block"
    assert set(payload) == {
        "file",
        "line",
        "kind",
        "rule",
        "level",
        "message",
        "fix_hint",
    }


_HOOKS = ConventionsStandard(
    modules=[Module(path="panel/src/hooks", purpose="hooks", forbidden=["component"])]
)
_COMPONENT_DEFS: list[Definition] = [("wrapper", 3, "component")]


def test_component_in_a_real_hooks_module_is_flagged() -> None:
    findings = check_placement("panel/src/hooks/use-tasks.tsx", _COMPONENT_DEFS, _HOOKS)
    assert [f.rule for f in findings] == ["no_components_in_hooks"]


@pytest.mark.parametrize(
    "rel_path",
    [
        "panel/src/hooks/__tests__/use-tasks.test.tsx",
        "panel/src/hooks/__tests__/test-utils.tsx",
        "panel/src/hooks/use-tasks.test.tsx",
        "panel/src/hooks/use-tasks.spec.ts",
    ],
)
def test_component_in_a_colocated_test_file_is_not_flagged(rel_path: str) -> None:
    # A test colocated inside a source module is treated like one under a
    # top-level tests/: a QueryClientProvider wrapper is a fixture, not a
    # misplaced production component.
    assert check_placement(rel_path, _COMPONENT_DEFS, _HOOKS) == []


@pytest.mark.parametrize(
    ("rel_path", "expected"),
    [
        ("panel/src/hooks/__tests__/use-x.test.tsx", True),
        ("panel/src/test/setup.ts", True),
        ("tests/unit/test_x.py", True),
        ("docs/site/page.ts", True),
        ("panel/src/hooks/use-x.test.tsx", True),
        ("panel/src/hooks/use-x.spec.tsx", True),
        ("panel/src/hooks/use-x.tsx", False),
        ("panel/src/hooks/use-latest-test.ts", False),
        ("roboco/api/routes/tests_admin.py", False),
        ("roboco/services/test_runner.py", False),
    ],
)
def test_is_non_source_path(rel_path: str, expected: bool) -> None:
    assert is_non_source_path(rel_path) is expected
