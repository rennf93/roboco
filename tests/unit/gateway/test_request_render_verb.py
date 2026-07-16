"""ContentActions.request_render — render a video composition to preview
frames so an agent verifies the RENDERED artifact, not just its source.

Guard matrix (flag off / no active task / non-video source / renderer
unconfigured / missing composition_id / bad composition_id-orientation-
frame_count / missing composition dir on disk / role gate), the dev and QA
success paths (frame extraction + marker payload shape), and a
VideoRendererError surfacing as a retryable rejection.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.foundation.policy.content import markers
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps
from roboco.services.video_renderer_client import VideoRendererError


def _make_actions(
    *,
    task_obj: MagicMock | None,
    role: str = "developer",
    team: str | None = "ux_ui",
    workspace: MagicMock | None = None,
) -> tuple[ContentActions, MagicMock]:
    task = AsyncMock()
    task.get_active_task_for_agent.return_value = task_obj
    task.session = MagicMock()
    task.session.flush = AsyncMock()
    task.heartbeat = AsyncMock()
    agent = MagicMock()
    agent.role = role
    agent.team = team
    task.agent_for = AsyncMock(return_value=agent)
    deps = ContentActionsDeps(
        task=task,
        git=MagicMock(),
        a2a=MagicMock(),
        journal=MagicMock(),
        workspace=workspace or MagicMock(),
        notifications=MagicMock(),
    )
    return ContentActions(deps), task


def _task(
    *,
    project_id: object | None = uuid4(),
    source: str = markers.VIDEO_TASK_SOURCE,
    branch_name: str | None = "feature/ux_ui/ABCD1234",
    draft: dict[str, object] | None = None,
) -> MagicMock:
    t = MagicMock()
    t.id = uuid4()
    t.project_id = project_id
    t.status = "in_progress"
    t.source = source
    t.branch_name = branch_name
    t.orchestration_markers = {"video_draft": draft} if draft else None
    return t


def _stub_project(
    monkeypatch: pytest.MonkeyPatch, *, slug: str = "demo-project"
) -> MagicMock:
    project = MagicMock(slug=slug, git_url="https://example.invalid/demo.git")
    project_service = MagicMock()
    project_service.get = AsyncMock(return_value=project)
    monkeypatch.setattr(
        "roboco.services.project.get_project_service", lambda _s: project_service
    )
    return project


def _stub_renderer(
    monkeypatch: pytest.MonkeyPatch,
    *,
    frames_tar_gz: bytes = b"",
    duration: float = 2.5,
    error: Exception | None = None,
) -> MagicMock:
    renderer = MagicMock()
    if error is not None:
        renderer.render_frames = AsyncMock(side_effect=error)
    else:
        renderer.render_frames = AsyncMock(return_value=(frames_tar_gz, duration))
    monkeypatch.setattr(
        "roboco.services.video_renderer_client.get_video_renderer",
        lambda: renderer,
    )
    return renderer


def _make_frames_tar(names: list[str]) -> bytes:
    """An in-memory tar.gz of a few frame files, matching what render_frames
    returns on success — the render loop extracts this straight to disk."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in names:
            data = f"fake-png-bytes-{name}".encode()
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _arm(
    monkeypatch: pytest.MonkeyPatch, *, renderer_url: str = "http://sidecar:3001"
) -> None:
    monkeypatch.setattr(settings, "video_engine_enabled", True)
    monkeypatch.setattr(settings, "video_renderer_base_url", renderer_url)


# --------------------------------------------------------------------------- #
# guard matrix
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_flag_off_refuses_before_task_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "video_engine_enabled", False)
    actions, task_svc = _make_actions(task_obj=None)

    env = await actions.request_render(agent_id=uuid4())

    assert env.error == "invalid_state"
    task_svc.get_active_task_for_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_active_task_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "video_engine_enabled", True)
    actions, _task_svc = _make_actions(task_obj=None)

    env = await actions.request_render(agent_id=uuid4())

    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_non_video_source_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "video_engine_enabled", True)
    t = _task(source="chore")
    actions, _task_svc = _make_actions(task_obj=t)

    env = await actions.request_render(agent_id=uuid4())

    assert env.error == "invalid_state"
    assert "video-authoring" in (env.message or "") + (env.remediate or "")


@pytest.mark.asyncio
async def test_renderer_unconfigured_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "video_engine_enabled", True)
    monkeypatch.setattr(settings, "video_renderer_base_url", "")
    t = _task()
    actions, _task_svc = _make_actions(task_obj=t)

    env = await actions.request_render(agent_id=uuid4())

    assert env.error == "invalid_state"
    assert "ROBOCO_VIDEO_RENDERER_BASE_URL" in (env.remediate or "")


@pytest.mark.asyncio
async def test_missing_composition_id_is_incomplete_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _arm(monkeypatch)
    t = _task(draft=None)
    actions, _task_svc = _make_actions(task_obj=t)

    env = await actions.request_render(agent_id=uuid4())

    assert env.error == "incomplete_input"
    assert "composition_id" in (env.missing or [])


@pytest.mark.asyncio
async def test_bad_composition_id_regex_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _arm(monkeypatch)
    t = _task()
    actions, _task_svc = _make_actions(task_obj=t)

    env = await actions.request_render(agent_id=uuid4(), composition_id="bad id!")

    assert env.error == "invalid_state"
    assert "not renderable" in (env.message or "")


@pytest.mark.asyncio
async def test_bad_orientation_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(monkeypatch)
    t = _task()
    actions, _task_svc = _make_actions(task_obj=t)

    env = await actions.request_render(
        agent_id=uuid4(), composition_id="release-v1", orientation="landscape"
    )

    assert env.error == "invalid_state"


@pytest.mark.asyncio
async def test_frame_count_out_of_range_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _arm(monkeypatch)
    t = _task()
    actions, _task_svc = _make_actions(task_obj=t)

    too_low = await actions.request_render(
        agent_id=uuid4(), composition_id="release-v1", frame_count=0
    )
    too_high = await actions.request_render(
        agent_id=uuid4(), composition_id="release-v1", frame_count=33
    )

    assert too_low.error == "invalid_state"
    assert too_high.error == "invalid_state"


@pytest.mark.asyncio
async def test_missing_composition_dir_on_disk_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _arm(monkeypatch)
    monkeypatch.setattr(settings, "workspaces_root", str(tmp_path / "workspaces"))
    _stub_project(monkeypatch)
    clone_root = tmp_path / "clone"
    clone_root.mkdir(parents=True)  # no motion/ tree at all
    workspace = MagicMock()
    workspace.get_clone_root_path.return_value = clone_root
    workspace.get_worktree_path.return_value = clone_root / ".worktrees" / "x"

    t = _task(draft={"composition_id": "release-v1"})
    actions, _task_svc = _make_actions(task_obj=t, workspace=workspace)

    env = await actions.request_render(agent_id=uuid4())

    assert env.error == "invalid_state"
    assert "motion/compositions/release-v1" in (env.message or "")


@pytest.mark.asyncio
async def test_other_role_not_authorized(monkeypatch: pytest.MonkeyPatch) -> None:
    _arm(monkeypatch)
    _stub_project(monkeypatch)
    t = _task(draft={"composition_id": "release-v1"})
    actions, _task_svc = _make_actions(task_obj=t, role="documenter", team="ux_ui")

    env = await actions.request_render(agent_id=uuid4())

    assert env.error == "not_authorized"


# --------------------------------------------------------------------------- #
# dev happy path — own working tree
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_dev_happy_path_extracts_frames_and_stamps_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _arm(monkeypatch)
    monkeypatch.setattr(settings, "workspaces_root", str(tmp_path / "workspaces"))
    _stub_project(monkeypatch, slug="demo-project")

    clone_root = tmp_path / "clone"
    (clone_root / "motion" / "compositions" / "release-v1").mkdir(parents=True)
    workspace = MagicMock()
    workspace.get_clone_root_path.return_value = clone_root
    workspace.get_worktree_path.return_value = clone_root / ".worktrees" / "deadbeef"

    t = _task(draft={"composition_id": "release-v1"})
    actions, task_svc = _make_actions(
        task_obj=t, role="developer", team="ux_ui", workspace=workspace
    )
    expected_frame_count = 2
    expected_duration = 3.2
    default_frame_count = 8
    tar_bytes = _make_frames_tar(["frame-0.png", "frame-1.png"])
    _stub_renderer(monkeypatch, frames_tar_gz=tar_bytes, duration=expected_duration)

    env = await actions.request_render(agent_id=uuid4())

    assert env.error is None
    assert env.status == t.status
    assert env.task_id == str(t.id)
    assert env.evidence is not None
    frames = env.evidence["frames"]
    assert len(frames) == expected_frame_count
    for p in frames:
        assert Path(p).is_file()
    assert env.evidence["duration_seconds"] == expected_duration
    assert env.evidence["source"] == "workspace"
    assert env.evidence["dirty"] is False
    assert env.evidence["rendered_by"]
    assert "note" in env.evidence
    assert "frames[]" in (env.next or "")

    payload = markers.get_render_preview(t)
    assert payload is not None
    assert payload["composition_id"] == "release-v1"
    assert payload["orientation"] == "vertical"
    assert payload["frame_count"] == default_frame_count
    assert payload["duration_seconds"] == expected_duration
    assert payload["source"] == "workspace"
    assert payload["dirty"] is False
    assert payload["frames"] == frames
    assert "at" in payload
    task_svc.session.flush.assert_awaited()
    task_svc.heartbeat.assert_awaited_once()


@pytest.mark.asyncio
async def test_explicit_composition_id_backfills_video_draft(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A dev who never called propose_video must still leave
    video_draft.composition_id stamped — the post-completion render loop
    keys on it and skips silently when absent (proven live)."""
    _arm(monkeypatch)
    monkeypatch.setattr(settings, "workspaces_root", str(tmp_path / "workspaces"))
    _stub_project(monkeypatch, slug="demo-project")

    clone_root = tmp_path / "clone"
    (clone_root / "motion" / "compositions" / "release-v1").mkdir(parents=True)
    workspace = MagicMock()
    workspace.get_clone_root_path.return_value = clone_root
    workspace.get_worktree_path.return_value = clone_root / ".worktrees" / "deadbeef"

    t = _task(draft=None)
    actions, _ = _make_actions(
        task_obj=t, role="developer", team="ux_ui", workspace=workspace
    )
    _stub_renderer(
        monkeypatch, frames_tar_gz=_make_frames_tar(["frame-0.png"]), duration=1.0
    )

    env = await actions.request_render(agent_id=uuid4(), composition_id="release-v1")

    assert env.error is None
    draft = markers.get_video_draft(t)
    assert draft is not None
    assert draft["composition_id"] == "release-v1"


@pytest.mark.asyncio
async def test_existing_video_draft_composition_id_not_overwritten(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _arm(monkeypatch)
    monkeypatch.setattr(settings, "workspaces_root", str(tmp_path / "workspaces"))
    _stub_project(monkeypatch, slug="demo-project")

    clone_root = tmp_path / "clone"
    for comp in ("release-v1", "release-v2"):
        (clone_root / "motion" / "compositions" / comp).mkdir(parents=True)
    workspace = MagicMock()
    workspace.get_clone_root_path.return_value = clone_root
    workspace.get_worktree_path.return_value = clone_root / ".worktrees" / "deadbeef"

    t = _task(draft={"composition_id": "release-v1", "occasion": "r1"})
    actions, _ = _make_actions(
        task_obj=t, role="developer", team="ux_ui", workspace=workspace
    )
    _stub_renderer(
        monkeypatch, frames_tar_gz=_make_frames_tar(["frame-0.png"]), duration=1.0
    )

    env = await actions.request_render(agent_id=uuid4(), composition_id="release-v2")

    assert env.error is None
    draft = markers.get_video_draft(t)
    assert draft is not None
    assert draft["composition_id"] == "release-v1"
    assert draft["occasion"] == "r1"


# --------------------------------------------------------------------------- #
# QA happy path — read-only branch export, never a working tree
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_qa_happy_path_uses_branch_export(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _arm(monkeypatch)
    monkeypatch.setattr(settings, "workspaces_root", str(tmp_path / "workspaces"))
    _stub_project(monkeypatch, slug="demo-project")

    scratch = tmp_path / "scratch"
    (scratch / "motion" / "compositions" / "release-v1").mkdir(parents=True)
    read_clone = tmp_path / "readclone"
    read_clone.mkdir()

    workspace = MagicMock()
    workspace.export_branch_motion = AsyncMock(return_value=scratch)
    workspace.ensure_read_clone = AsyncMock(return_value=read_clone)

    t = _task(
        draft={"composition_id": "release-v1"},
        branch_name="feature/ux_ui/ABCD1234",
    )
    actions, task_svc = _make_actions(
        task_obj=t, role="qa", team=None, workspace=workspace
    )
    tar_bytes = _make_frames_tar(["frame-0.png"])
    _stub_renderer(monkeypatch, frames_tar_gz=tar_bytes, duration=1.0)

    env = await actions.request_render(agent_id=uuid4())

    assert env.error is None
    assert env.evidence is not None
    assert env.evidence["source"] == "branch"
    assert env.evidence["dirty"] is False
    assert len(env.evidence["frames"]) == 1
    workspace.export_branch_motion.assert_awaited_once()
    called_project, called_branch = workspace.export_branch_motion.call_args.args
    assert called_project.slug == "demo-project"
    assert called_branch == "feature/ux_ui/ABCD1234"
    payload = markers.get_render_preview(t)
    assert payload is not None
    assert payload["source"] == "branch"
    task_svc.heartbeat.assert_awaited_once()


@pytest.mark.asyncio
async def test_qa_without_branch_name_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _arm(monkeypatch)
    _stub_project(monkeypatch)
    t = _task(draft={"composition_id": "release-v1"}, branch_name=None)
    actions, _task_svc = _make_actions(task_obj=t, role="qa", team=None)

    env = await actions.request_render(agent_id=uuid4())

    assert env.error == "invalid_state"


# --------------------------------------------------------------------------- #
# renderer failure
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_video_renderer_error_is_retryable_invalid_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _arm(monkeypatch)
    monkeypatch.setattr(settings, "workspaces_root", str(tmp_path / "workspaces"))
    _stub_project(monkeypatch, slug="demo-project")

    clone_root = tmp_path / "clone"
    (clone_root / "motion" / "compositions" / "release-v1").mkdir(parents=True)
    workspace = MagicMock()
    workspace.get_clone_root_path.return_value = clone_root
    workspace.get_worktree_path.return_value = clone_root / ".worktrees" / "x"

    t = _task(draft={"composition_id": "release-v1"})
    actions, _task_svc = _make_actions(
        task_obj=t, role="developer", team="ux_ui", workspace=workspace
    )
    _stub_renderer(monkeypatch, error=VideoRendererError("sidecar unreachable"))

    env = await actions.request_render(agent_id=uuid4())

    assert env.error == "invalid_state"
    assert "retry" in (env.remediate or "").lower()
    assert markers.get_render_preview(t) is None
