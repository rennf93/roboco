"""The RENDER_VERIFIED tracing requirement — i_am_done's video-artifact gate.

Pure unit tests against ``foundation.policy.tracing`` (no DB, no
choreographer): the checker itself, its registration in
``VERB_REQUIREMENTS["i_am_done"]``, and the "non-video task is untouched"
contract.
"""

from __future__ import annotations

from types import SimpleNamespace

from roboco.foundation.policy import tracing as tr
from roboco.foundation.policy.content import markers


def test_render_verified_is_required_by_i_am_done() -> None:
    assert tr.Requirement.RENDER_VERIFIED in tr.VERB_REQUIREMENTS["i_am_done"]


def test_render_verified_not_required_by_submit_up_or_submit_root() -> None:
    assert tr.Requirement.RENDER_VERIFIED not in tr.VERB_REQUIREMENTS["submit_up"]
    assert tr.Requirement.RENDER_VERIFIED not in tr.VERB_REQUIREMENTS["submit_root"]


def test_non_video_task_passes_regardless_of_marker() -> None:
    task = SimpleNamespace(source="code", orchestration_markers=None)
    assert tr._check_render_verified(task, tr.GateContext()) == []


def test_video_task_without_preview_fails() -> None:
    task = SimpleNamespace(source=markers.VIDEO_TASK_SOURCE, orchestration_markers=None)
    assert tr._check_render_verified(task, tr.GateContext()) == ["render_preview"]


def test_video_task_with_preview_passes() -> None:
    task = SimpleNamespace(
        source=markers.VIDEO_TASK_SOURCE,
        orchestration_markers={markers.RENDER_PREVIEW: {"frames": ["a.png"]}},
    )
    assert tr._check_render_verified(task, tr.GateContext()) == []


def test_i_am_done_requirements_include_the_pre_existing_set_too() -> None:
    """Adding RENDER_VERIFIED must not have dropped any prior requirement."""
    required = tr.VERB_REQUIREMENTS["i_am_done"]
    for expected in (
        tr.Requirement.COMMITS_AT_LEAST_ONE,
        tr.Requirement.PR_OPEN,
        tr.Requirement.PROGRESS_AT_LEAST_ONE,
        tr.Requirement.SELF_VERIFIED,
        tr.Requirement.JOURNAL_REFLECT,
        tr.Requirement.JOURNAL_DURING_WORK_AT_LEAST_ONE,
        tr.Requirement.ACCEPTANCE_CRITERIA_ADDRESSED,
        tr.Requirement.DEV_NOTES_MIN_CHARS,
        tr.Requirement.FINDINGS_ADDRESSED,
    ):
        assert expected in required
