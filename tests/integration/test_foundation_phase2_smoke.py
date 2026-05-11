"""Foundation Phase 2 smoke gate — every journal:X check goes through tracing."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest
from roboco.foundation.policy import lifecycle as spec
from roboco.foundation.policy import tracing

_GATEWAY_DIR = Path(__file__).resolve().parents[2] / "roboco" / "services" / "gateway"


def _enclosing_function(tree: ast.AST, lineno: int) -> str | None:
    """Return the name of the (async) function whose body contains ``lineno``."""
    candidate: str | None = None
    candidate_start = -1
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            start = node.lineno
            end = getattr(node, "end_lineno", None) or start
            if start <= lineno <= end and start > candidate_start:
                candidate = node.name
                candidate_start = start
    return candidate


def test_no_inline_has_decision_for_task_remains_in_choreographer():
    """All journal:decision checks must use tracing.check_requirements via the
    unified helpers (_check_pm_decision_required, _check_complete_gates,
    _check_submit_up_gates, _check_tracing_gates, _check_claim_journal_at_claim,
    _post_claim_journal_gate)."""
    allowed_helpers = {
        "_check_pm_decision_required",
        "_check_complete_gates",
        "_check_submit_up_gates",
        "_check_tracing_gates",
        "_check_claim_journal_at_claim",
        "_post_claim_journal_gate",
    }
    suspicious: list[str] = []
    for py_path in _GATEWAY_DIR.rglob("*.py"):
        source = py_path.read_text()
        if "has_decision_for_task" not in source:
            continue
        tree = ast.parse(source, filename=str(py_path))
        for lineno, line in enumerate(source.splitlines(), start=1):
            if "has_decision_for_task" not in line:
                continue
            enclosing = _enclosing_function(tree, lineno)
            # Helper definition / docstring references don't count; only
            # call-site usages matter, but if the line is inside a helper
            # whose body is allowed, we accept it.
            if enclosing in allowed_helpers:
                continue
            # Allow references within docstrings (no executable impact).
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"'):
                continue
            suspicious.append(f"{py_path}:{lineno}:{line.strip()}")
    assert suspicious == [], f"inline has_decision_for_task remains: {suspicious}"


def test_tracing_gate_module_removed():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("roboco.services.gateway.tracing_gate")


def test_every_intent_verb_has_tracing_decision():
    """Mirror of the foundation parity test, as a smoke-gate."""
    intent_verbs = set(spec._INTENT_VERBS.keys())
    in_table = set(tracing.VERB_REQUIREMENTS)
    in_waived = set(tracing.VERBS_WITHOUT_TRACING)
    assert intent_verbs - in_table - in_waived == set(), (
        f"verbs in lifecycle.spec without tracing decision: "
        f"{intent_verbs - in_table - in_waived}"
    )


def test_no_dangling_requirements():
    """Every Requirement value is referenced by at least one verb."""
    used = set()
    for reqs in tracing.VERB_REQUIREMENTS.values():
        used.update(reqs)
    assert set(tracing.Requirement) - used == set()
