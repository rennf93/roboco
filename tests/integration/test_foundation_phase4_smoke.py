"""Foundation Phase 4 smoke gate — final package layout."""

from __future__ import annotations

import importlib
import inspect
import subprocess
from pathlib import Path

import pytest
from roboco.api import deps as api_deps
from roboco.api.routes.v1 import _role_dep as v1_role_dep


def test_lifecycle_module_lives_in_foundation():
    """Canonical import path is foundation.policy.lifecycle."""
    lifecycle = importlib.import_module("roboco.foundation.policy.lifecycle")
    assert hasattr(lifecycle, "Role")
    assert hasattr(lifecycle, "Status")
    assert hasattr(lifecycle, "_INTENT_VERBS")


def test_legacy_lifecycle_package_removed():
    """Legacy roboco.lifecycle package is gone."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("roboco.lifecycle")


def test_legacy_lifecycle_spec_module_removed():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("roboco.lifecycle.spec")


def test_foundation_policy_complete():
    """All 6 policy domains exist in foundation."""
    for mod in (
        "lifecycle",
        "tracing",
        "journaling",
        "task_completeness",
        "communications",
        "agent_loop",
    ):
        importlib.import_module(f"roboco.foundation.policy.{mod}")


def test_no_lifecycle_imports_in_production():
    """Production code (roboco/) imports from foundation directly, no legacy paths."""
    proc = subprocess.run(
        [
            "grep",
            "-rn",
            "from roboco.lifecycle\\|import roboco.lifecycle",
            "roboco/",
            "--include=*.py",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    suspicious = []
    for line in proc.stdout.splitlines():
        if not line:
            continue
        # Allow docstring/comment mentions (the line contains 'lifecycle' but
        # not as an actual import statement). A bare-bones filter:
        suspicious.append(line)
    assert suspicious == [], f"legacy lifecycle imports remain: {suspicious}"


def test_route_guard_role_sets_derive_from_foundation():
    """api/deps.py + v1/_role_dep.py use foundation Role-set composition."""
    deps_src = inspect.getsource(api_deps)
    role_dep_src = inspect.getsource(v1_role_dep)
    assert (
        "from roboco.foundation.identity" in deps_src
        or "from roboco.foundation import" in deps_src
    )
    assert "Role." in role_dep_src  # uses Role enum members, not raw strings


def test_make_foundation_check_target_exists():
    """Drift gate target is present in Makefile."""
    makefile = Path("Makefile").read_text(encoding="utf-8")
    assert "foundation-check:" in makefile
