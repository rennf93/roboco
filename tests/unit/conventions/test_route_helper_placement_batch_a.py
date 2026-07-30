"""Regression guard for Batch A of the route-helper cleanup.

``roboco/api/routes/tasks.py``, ``a2a.py``, ``orchestrator.py``, ``video.py``,
``journals.py``, ``v1/_role_dep.py``, ``roadmap.py`` and ``prompter_live.py``
were audited against the real conventions validator (not a crude top-level
scan) and every module-level definition in them is already a proper
``@router``/``@app`` route handler (or, for ``v1/_role_dep.py``, not a
function definition at all) — there is nothing to relocate. This test pins
that fact so a future top-level helper slipping into one of these files is
caught by the gate instead of silently reintroducing the violation.
"""

from __future__ import annotations

from pathlib import Path

from roboco.conventions.runner import run
from roboco.conventions.scan import derive_from_scan
from roboco.foundation.policy.conventions.effective_map import effective_map
from roboco.foundation.policy.conventions.models import ConventionsStandard

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BATCH_A_FILES = [
    "roboco/api/routes/tasks.py",
    "roboco/api/routes/a2a.py",
    "roboco/api/routes/orchestrator.py",
    "roboco/api/routes/video.py",
    "roboco/api/routes/journals.py",
    "roboco/api/routes/v1/_role_dep.py",
    "roboco/api/routes/roadmap.py",
    "roboco/api/routes/prompter_live.py",
]


def _effective_standard() -> ConventionsStandard:
    derived = derive_from_scan(_REPO_ROOT)
    committed_path = _REPO_ROOT / ".roboco" / "conventions.yml"
    committed = (
        ConventionsStandard.parse_yaml(committed_path.read_text())
        if committed_path.is_file()
        else None
    )
    return effective_map(derived, committed)


def test_batch_a_route_files_have_no_helper_placement_findings() -> None:
    standard = _effective_standard()
    findings = run(_REPO_ROOT, _BATCH_A_FILES, standard)
    helper_findings = [f for f in findings if f.kind == "helper"]
    assert helper_findings == []
