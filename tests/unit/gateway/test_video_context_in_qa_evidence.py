"""QA claim_review evidence carries a video-artifact context for
video-source tasks — pointing QA at the rendered artifact, not just source."""

from __future__ import annotations

from unittest.mock import MagicMock

from roboco.foundation.policy.content import markers
from roboco.services.gateway.choreographer import Choreographer
from roboco.services.gateway.evidence_builder import build_evidence_for_task


def _stub_task() -> MagicMock:
    task = MagicMock()
    task.pr_number = None
    task.pr_url = None
    task.commits = []
    task.dev_notes = None
    task.acceptance_criteria_status = []
    task.source = "code"
    task.orchestration_markers = None
    return task


def test_video_context_none_for_non_video_task() -> None:
    assert Choreographer._qa_video_context(_stub_task()) is None


def test_video_context_present_for_video_task_with_render_preview() -> None:
    task = _stub_task()
    task.source = markers.VIDEO_TASK_SOURCE
    task.orchestration_markers = {
        markers.VIDEO_DRAFT: {"composition_id": "intro-v1"},
        markers.RENDER_PREVIEW: {"frames": ["a.png", "b.png"]},
    }
    ctx = Choreographer._qa_video_context(task)
    assert ctx is not None
    assert ctx["composition_id"] == "intro-v1"
    assert ctx["render_preview"] == {"frames": ["a.png", "b.png"]}
    assert "request_render" in ctx["note"]


def test_video_context_render_preview_none_without_marker() -> None:
    task = _stub_task()
    task.source = markers.VIDEO_TASK_SOURCE
    task.orchestration_markers = {markers.VIDEO_DRAFT: {"composition_id": "intro-v1"}}
    ctx = Choreographer._qa_video_context(task)
    assert ctx is not None
    assert ctx["composition_id"] == "intro-v1"
    assert ctx["render_preview"] is None


def test_evidence_payload_includes_video_context() -> None:
    video_context = {"composition_id": "x", "render_preview": None, "note": "n"}
    ev = build_evidence_for_task(
        _stub_task(),
        journal_highlights=[],
        files_changed=[],
        video_context=video_context,
    )
    assert ev.as_dict()["video_context"] == video_context


def test_evidence_payload_video_context_default_absent() -> None:
    ev = build_evidence_for_task(_stub_task(), journal_highlights=[], files_changed=[])
    assert ev.video_context is None
    assert "video_context" not in ev.as_dict()
