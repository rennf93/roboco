"""DocsService path-containment guard (CodeQL path-traversal fix, 2026-06-29).

The old ``if ".." in path`` substring guard is bypassable: ``Path(base) /
"/etc/passwd"`` evaluates to ``/etc/passwd`` (pathlib resets on an absolute
right operand), so an absolute ``path`` escapes ``DOCS_BASE_PATH`` and the
``unlink``/``read_text`` sink hits an arbitrary file. These tests pin a
resolve-and-contain guard on ``read_doc`` / ``delete_doc`` and the pure
helper behind it.

``read_doc`` / ``delete_doc`` only consult the agent catalog (no DB), so a
``MagicMock`` session is enough — no Postgres required.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from roboco.services.base import ValidationError
from roboco.services.docs import DocsService, _resolve_contained_path

if TYPE_CHECKING:
    from pathlib import Path

    from pytest import TempPathFactory


# ---------------------------------------------------------------------------
# Pure helper
# ---------------------------------------------------------------------------


def test_resolve_rejects_absolute_path(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        _resolve_contained_path(tmp_path, "/etc/passwd")


def test_resolve_rejects_traversal_escape(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        _resolve_contained_path(tmp_path, "backend/../../etc/passwd")


def test_resolve_rejects_empty(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        _resolve_contained_path(tmp_path, "")


def test_resolve_rejects_nul(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        _resolve_contained_path(tmp_path, "backend\x00evil.md")


def test_resolve_rejects_dotdot(tmp_path: Path) -> None:
    # The existing '..' policy is preserved (no behavior change).
    with pytest.raises(ValidationError):
        _resolve_contained_path(tmp_path, "backend/../etc/passwd")


def test_resolve_accepts_nested_relative(tmp_path: Path) -> None:
    resolved = _resolve_contained_path(tmp_path, "backend/api/endpoints.md")
    assert resolved == (tmp_path / "backend/api/endpoints.md").resolve()
    assert tmp_path.resolve() in resolved.parents


# ---------------------------------------------------------------------------
# Service sinks — read_doc / delete_doc must reject the absolute bypass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_doc_rejects_absolute_path(
    tmp_path: Path, tmp_path_factory: TempPathFactory
) -> None:
    # A file OUTSIDE the docs base — an absolute `path` must not reach its sink.
    outside = tmp_path_factory.mktemp("outside") / "secret.md"
    outside.write_text("secret", encoding="utf-8")
    svc = DocsService(MagicMock())
    with (
        patch("roboco.services.docs.DOCS_BASE_PATH", tmp_path),
        pytest.raises(ValidationError),
    ):
        await svc.read_doc(agent_id="be-doc", path=str(outside))
    # Unchanged: the outside file is intact (the bypass would have read it).
    assert outside.read_text(encoding="utf-8") == "secret"


@pytest.mark.asyncio
async def test_delete_doc_rejects_absolute_path(
    tmp_path: Path, tmp_path_factory: TempPathFactory
) -> None:
    # A file OUTSIDE the docs base — an absolute `path` must not reach unlink.
    outside = tmp_path_factory.mktemp("outside") / "marker.md"
    outside.write_text("marker", encoding="utf-8")
    svc = DocsService(MagicMock())
    with (
        patch("roboco.services.docs.DOCS_BASE_PATH", tmp_path),
        pytest.raises(ValidationError),
    ):
        await svc.delete_doc(agent_id="be-doc", path=str(outside))
    # The bypass would have DELETED this file — it must survive.
    assert outside.exists()


@pytest.mark.asyncio
async def test_read_doc_rejects_traversal(tmp_path: Path) -> None:
    svc = DocsService(MagicMock())
    with (
        patch("roboco.services.docs.DOCS_BASE_PATH", tmp_path),
        pytest.raises(ValidationError),
    ):
        await svc.read_doc(agent_id="be-doc", path="backend/../../etc/passwd")


@pytest.mark.asyncio
async def test_read_doc_normal_relative_still_works(tmp_path: Path) -> None:
    # Regression guard: a legit nested doc reads fine under the new guard.
    doc = tmp_path / "backend" / "api" / "endpoints.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("# Endpoints", encoding="utf-8")
    svc = DocsService(MagicMock())
    with patch("roboco.services.docs.DOCS_BASE_PATH", tmp_path):
        content, size = await svc.read_doc(
            agent_id="be-doc", path="backend/api/endpoints.md"
        )
    assert content == "# Endpoints"
    assert size == len("# Endpoints")
