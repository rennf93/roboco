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
_MAX_FIXTURES = 8


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


def test_target_role_is_developer_for_every_fixture() -> None:
    """Matches EvalRunner.run_cohort's current scope cut (see runner.py's
    module docstring) — every fixture must be runnable by the one role the
    bench supports today."""
    for f in FIXTURES:
        assert f.target_role == "developer", f.key


def test_bench_task_spec_is_frozen() -> None:
    spec = FIXTURES[0]
    assert isinstance(spec, BenchTaskSpec)
    mutable_view = cast("Any", spec)
    with pytest.raises(dataclasses.FrozenInstanceError):
        mutable_view.title = "mutated"
