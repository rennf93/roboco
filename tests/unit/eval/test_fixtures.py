"""Schema checks for the golden-task fixtures (roboco/eval/fixtures.py).

Nothing here touches a DB or the network — these are pure sanity checks on
the static FIXTURES tuple so a malformed fixture (a duplicate key, a fixture
file that escapes its own bench/<key>/ namespace and could collide with
another fixture's repo state, an empty brief) is caught before it ever
reaches the runner.
"""

from __future__ import annotations

import dataclasses
from typing import Any, cast

import pytest
from roboco.eval.fixtures import FIXTURES, BenchTaskSpec

_MIN_FIXTURES = 5
_MAX_FIXTURES = 12


def test_fixture_keys_are_unique() -> None:
    keys = [f.key for f in FIXTURES]
    assert len(keys) == len(set(keys)), f"duplicate fixture keys: {keys}"


def test_at_least_five_fixtures() -> None:
    # The task calls for 5-8 canonical fixtures.
    assert _MIN_FIXTURES <= len(FIXTURES) <= _MAX_FIXTURES, len(FIXTURES)


def test_every_fixture_has_a_non_empty_brief() -> None:
    for f in FIXTURES:
        assert f.title.strip(), f.key
        assert f.description.strip(), f.key
        assert f.acceptance_criteria, f"{f.key} has no acceptance criteria"
        assert all(c.strip() for c in f.acceptance_criteria), f.key
        assert f.expectations.strip(), f"{f.key} has no judge expectations note"


def test_repo_files_are_namespaced_under_bench_key() -> None:
    """Every fixture's seeded file lives under bench/<its own key>/ so
    sequential fixtures sharing one project's git history never collide."""
    for f in FIXTURES:
        assert f.repo_files, f"{f.key} seeds no repo files"
        prefix = f"bench/{f.key}/"
        for rel_path, content in f.repo_files:
            assert rel_path.startswith(prefix), (
                f"{f.key}: {rel_path!r} escapes its own {prefix!r} namespace"
            )
            assert ".." not in rel_path, f"{f.key}: {rel_path!r} looks like a traversal"
            assert content, f"{f.key}: {rel_path!r} has empty content"


def test_repo_file_paths_within_a_fixture_are_unique() -> None:
    for f in FIXTURES:
        paths = [rel_path for rel_path, _content in f.repo_files]
        assert len(paths) == len(set(paths)), f"{f.key}: duplicate paths {paths}"


def test_target_role_is_known_for_every_fixture() -> None:
    """Every fixture targets a role the bench supports: developer, qa,
    cell_pm, or main_pm (see BenchTaskSpec.target_role docstring)."""
    valid_roles = {"developer", "qa", "cell_pm", "main_pm"}
    for f in FIXTURES:
        assert f.target_role in valid_roles, f"{f.key}: unknown role {f.target_role!r}"


def test_bench_task_spec_is_frozen() -> None:
    spec = FIXTURES[0]
    assert isinstance(spec, BenchTaskSpec)
    mutable_view = cast("Any", spec)
    with pytest.raises(dataclasses.FrozenInstanceError):
        mutable_view.title = "mutated"


def test_developer_fixtures_have_default_new_fields() -> None:
    """The 6 original developer fixtures are backward-compatible: the new
    optional fields carry their defaults (entry_status=pending, no injected
    defect, not a parent, no expected coverage, no expected catch gate)."""
    for f in FIXTURES:
        if f.target_role == "developer":
            assert f.entry_status == "pending", f.key
            assert f.injected_defect is None, f.key
            assert f.is_parent is False, f.key
            assert f.expected_coverage == (), f.key
            assert f.expected_catch_gate is None, f.key


def test_qa_fixture_has_injected_defect_and_awaiting_qa_entry() -> None:
    """QA fixtures enter at awaiting_qa with a pre-built PR and carry an
    injected_defect the QA agent must catch."""
    qa_fixtures = [f for f in FIXTURES if f.target_role == "qa"]
    assert qa_fixtures, "no QA fixture found"
    for f in qa_fixtures:
        assert f.entry_status == "awaiting_qa", f.key
        assert f.injected_defect is not None, f"{f.key} has no injected_defect"
        assert f.is_parent is False, f.key


def test_pm_fixture_is_parent_with_expected_coverage() -> None:
    """PM fixtures are parent tasks with expected_coverage the PM must map
    via covers_parent_criteria."""
    pm_fixtures = [f for f in FIXTURES if f.target_role in ("cell_pm", "main_pm")]
    assert pm_fixtures, "no PM fixture found"
    for f in pm_fixtures:
        assert f.is_parent is True, f"{f.key} is not a parent"
        assert f.expected_coverage, f"{f.key} has no expected_coverage"
        assert f.injected_defect is None, f.key
        assert f.expected_catch_gate is None, f.key


_VALID_CATCH_GATES = {"qa_ac_stamp", "conventions_check", "pr_gate"}
_MIN_SEEDED_DEFECT_FIXTURES = 4


def test_seeded_defect_fixtures_name_a_valid_catch_gate() -> None:
    """Every seeded-defect fixture (expected_catch_gate set) is a QA-entry
    fixture and names one of roboco.eval.runner's three recognized gate
    vocabulary values, so a miss during scoring can name the layer that
    missed rather than just 'something failed'."""
    seeded = [f for f in FIXTURES if f.expected_catch_gate is not None]
    assert len(seeded) >= _MIN_SEEDED_DEFECT_FIXTURES, (
        f"expected at least {_MIN_SEEDED_DEFECT_FIXTURES} seeded-defect "
        f"fixtures, found {len(seeded)}"
    )
    for f in seeded:
        assert f.expected_catch_gate in _VALID_CATCH_GATES, (
            f"{f.key}: unrecognized catch gate {f.expected_catch_gate!r}"
        )
        assert f.target_role == "qa", f"{f.key}: seeded-defect fixture must target qa"
        assert f.entry_status == "awaiting_qa", f.key
        assert f.injected_defect is not None, f"{f.key} has no injected_defect"


def test_seeded_defect_fixtures_cover_the_required_defect_classes() -> None:
    """The four required defect classes are each represented: a dropped
    acceptance criterion, a conventions/placement violation, a security
    flaw, and a vacuously-passing test."""
    gates_by_key = {f.key: f.expected_catch_gate for f in FIXTURES}
    assert gates_by_key.get("qa-catch-dropped-ac") == "qa_ac_stamp"
    assert gates_by_key.get("qa-catch-conventions-misplacement") == "conventions_check"
    assert gates_by_key.get("qa-catch-security-flaw") == "qa_ac_stamp"
    assert gates_by_key.get("qa-catch-vacuous-test") == "qa_ac_stamp"
