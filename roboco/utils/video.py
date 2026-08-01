"""Video preview-frame helpers — path resolution and frame-filename parsing
for the CEO preview-frames routes. Pure path/listing utilities; no DB access,
no route definitions."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from roboco.config import settings

if TYPE_CHECKING:
    from uuid import UUID

_FRAME_NAME_RE = re.compile(r"^frame-(\d+)-of-(\d+)-at-([\d.]+)s\.png$")


class ParsedFrame(NamedTuple):
    """One preview frame parsed from its self-describing filename."""

    frame_index: int
    file: str
    timestamp_seconds: float


def previews_root(task_id: UUID, project_slug: str) -> Path:
    """The container-shared preview-frames dir for a video-authoring task:
    ``{workspaces_root}/{project}/.previews/{task8}/``. Every agent container
    mounts the same ``/data/workspaces``, so the CEO's container reads the
    frames the dev's container rendered."""
    return Path(settings.workspaces_root) / project_slug / ".previews" / task_id.hex[:8]


def list_orientation_frames(orientation_dir: Path) -> list[ParsedFrame]:
    """List the preview frames in ``orientation_dir``, parsed from the
    self-describing ``frame-<idx>-of-<n>-at-<t>s.png`` filenames. Returns
    ``ParsedFrame`` namedtuples sorted by index. Returns an empty list when
    the directory doesn't exist or holds no matching files."""
    frames: list[ParsedFrame] = []
    if not orientation_dir.is_dir():
        return frames
    for entry in sorted(orientation_dir.iterdir()):
        m = _FRAME_NAME_RE.match(entry.name)
        if m is None or not entry.is_file():
            continue
        frames.append(
            ParsedFrame(
                frame_index=int(m.group(1)),
                file=entry.name,
                timestamp_seconds=float(m.group(3)),
            )
        )
    return frames
