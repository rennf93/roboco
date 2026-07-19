"""DocsService coverage — write/read/list/delete docs + RAG dedup."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.config import settings
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import (
    TaskNature,
    TaskStatus,
    TaskType,
)
from roboco.models.task import DocRef
from roboco.services.base import (
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from roboco.services.docs import (
    DocsService,
    WriteDocInput,
    _refused_doc_types,
    get_docs_service,
)
from sqlalchemy import select

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def docs_setup(
    db_session: AsyncSession,
) -> AsyncIterator[dict]:
    """Seed an agent + project + task."""
    agent = AgentTable(
        id=uuid4(),
        name="Doc",
        slug=f"be-doc-{uuid4().hex[:8]}",
        role=AgentRole.DOCUMENTER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="doc",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(agent)
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="Doc-Proj",
        slug=f"doc-proj-{uuid4().hex[:8]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=agent.id,
    )
    db_session.add(project)
    await db_session.flush()
    task = TaskTable(
        id=uuid4(),
        title="t",
        description="d",
        acceptance_criteria=["ac"],
        status=TaskStatus.PENDING,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=project.id,
        created_by=agent.id,
        team=Team.BACKEND,
    )
    db_session.add(task)
    await db_session.flush()
    yield {
        "svc": DocsService(db_session),
        "agent_id": agent.id,
        "task_id": task.id,
    }


# ---------------------------------------------------------------------------
# get_docs_service factory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_docs_service_factory(docs_setup: dict) -> None:
    svc = docs_setup["svc"]
    assert svc is not None


@pytest.mark.asyncio
async def test_factory_function(db_session: AsyncSession) -> None:
    svc = get_docs_service(db_session)
    assert isinstance(svc, DocsService)


# ---------------------------------------------------------------------------
# Permissions / validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_doc_unknown_agent_team(docs_setup: dict) -> None:
    """Agent with no team mapping → ValidationError."""
    svc = docs_setup["svc"]
    with pytest.raises(ValidationError, match="Unknown agent team"):
        await svc.write_doc(
            agent_id="ghost-agent",
            req=WriteDocInput(
                task_id=docs_setup["task_id"],
                filename="x.md",
                doc_type="api",
                title="Title",
                content="Content",
            ),
        )


@pytest.mark.asyncio
async def test_write_doc_role_unauthorized(docs_setup: dict) -> None:
    """Agent role not in WRITE_ROLES → UnauthorizedError."""
    svc = docs_setup["svc"]
    # be-dev-1 is a developer (not in WRITE_ROLES = {"documenter", "cell_pm"})
    with pytest.raises(UnauthorizedError, match="cannot write"):
        await svc.write_doc(
            agent_id="be-dev-1",
            req=WriteDocInput(
                task_id=docs_setup["task_id"],
                filename="x.md",
                doc_type="api",
                title="Title",
                content="Content",
            ),
        )


@pytest.mark.asyncio
async def test_write_doc_invalid_doc_type(docs_setup: dict) -> None:
    svc = docs_setup["svc"]
    with pytest.raises(ValidationError, match="Unknown doc_type"):
        await svc.write_doc(
            agent_id="be-doc",
            req=WriteDocInput(
                task_id=docs_setup["task_id"],
                filename="x.md",
                doc_type="bogus",
                title="Title",
                content="Content",
            ),
        )


@pytest.mark.asyncio
async def test_write_doc_user_facing_refused(docs_setup: dict) -> None:
    """doc_type='user_facing' is a recognized value (not a generic 'Unknown
    doc_type') but is structurally refused: this store's buckets are all
    excluded from the published site. The guidance names the roboco-website
    project and the 3-edit pattern instead of silently landing an
    unpublished write (docs-site-split Phase 2)."""
    svc = docs_setup["svc"]
    with pytest.raises(ValidationError, match="roboco-website") as exc_info:
        await svc.write_doc(
            agent_id="be-doc",
            req=WriteDocInput(
                task_id=docs_setup["task_id"],
                filename="x.md",
                doc_type="user_facing",
                title="Title",
                content="Content",
            ),
        )
    assert "Unknown doc_type" not in str(exc_info.value)
    assert "docs.roboco.tech" in str(exc_info.value)


def test_refused_doc_types_uses_configured_docs_site(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deployer's own docs-site slug/URL (ROBOCO_DOCS_SITE_*) reaches the
    refusal message instead of RoboCo's own docs site."""
    monkeypatch.setattr(settings, "docs_site_project_slug", "acme-docs")
    monkeypatch.setattr(settings, "docs_site_public_url", "docs.acme.example")
    message = _refused_doc_types()["user_facing"]
    assert "acme-docs" in message
    assert "docs.acme.example" in message
    assert "roboco-website" not in message
    assert "docs.roboco.tech" not in message


def test_refused_doc_types_falls_back_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty docs-site slug/URL degrades to a generic message, never a
    bare empty string spliced into the refusal text."""
    monkeypatch.setattr(settings, "docs_site_project_slug", "")
    monkeypatch.setattr(settings, "docs_site_public_url", "")
    message = _refused_doc_types()["user_facing"]
    assert "your docs-site project" in message
    assert "roboco-website" not in message
    assert "docs.roboco.tech" not in message


@pytest.mark.asyncio
async def test_write_doc_path_traversal_in_filename(docs_setup: dict) -> None:
    svc = docs_setup["svc"]
    with pytest.raises(ValidationError, match="path separators"):
        await svc.write_doc(
            agent_id="be-doc",
            req=WriteDocInput(
                task_id=docs_setup["task_id"],
                filename="../evil.md",
                doc_type="api",
                title="Title",
                content="Content",
            ),
        )
    with pytest.raises(ValidationError, match="path separators"):
        await svc.write_doc(
            agent_id="be-doc",
            req=WriteDocInput(
                task_id=docs_setup["task_id"],
                filename="sub/evil.md",
                doc_type="api",
                title="Title",
                content="Content",
            ),
        )
    with pytest.raises(ValidationError, match="path separators"):
        await svc.write_doc(
            agent_id="be-doc",
            req=WriteDocInput(
                task_id=docs_setup["task_id"],
                filename=r"sub\evil.md",
                doc_type="api",
                title="Title",
                content="Content",
            ),
        )


# ---------------------------------------------------------------------------
# Write doc (creating new)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_doc_creates_new(docs_setup: dict, tmp_path: Path) -> None:
    """Happy path — create new doc, RAG dedup returns None."""
    svc = docs_setup["svc"]
    with (
        patch("roboco.services.docs.DOCS_BASE_PATH", tmp_path),
        patch.object(svc, "_find_similar_doc", AsyncMock(return_value=None)),
        patch.object(svc, "_index_doc_in_rag", AsyncMock(return_value=None)),
    ):
        rel_path, doc_ref, is_update = await svc.write_doc(
            agent_id="be-doc",
            req=WriteDocInput(
                task_id=docs_setup["task_id"],
                filename="example.md",
                doc_type="api",
                title="Title",
                content="# Hello",
            ),
        )
    assert is_update is False
    assert rel_path.endswith("example.md")
    assert doc_ref.title == "Title"


@pytest.mark.asyncio
async def test_write_doc_creates_new_subfolder_empty(
    docs_setup: dict, tmp_path: Path
) -> None:
    """doc_type=readme has empty subfolder → no subfolder in path."""
    svc = docs_setup["svc"]
    with (
        patch("roboco.services.docs.DOCS_BASE_PATH", tmp_path),
        patch.object(svc, "_find_similar_doc", AsyncMock(return_value=None)),
        patch.object(svc, "_index_doc_in_rag", AsyncMock(return_value=None)),
    ):
        rel_path, _, _ = await svc.write_doc(
            agent_id="be-doc",
            req=WriteDocInput(
                task_id=docs_setup["task_id"],
                filename="README.md",
                doc_type="readme",
                title="Readme",
                content="# Project",
            ),
        )
    # Should be backend/README.md (no subfolder for readme).
    assert rel_path == "backend/README.md"


@pytest.mark.asyncio
async def test_write_doc_updates_existing(docs_setup: dict, tmp_path: Path) -> None:
    """When _find_similar_doc returns a path AND the filename matches, update."""
    svc = docs_setup["svc"]
    existing_path = "backend/api/existing.md"
    with (
        patch("roboco.services.docs.DOCS_BASE_PATH", tmp_path),
        patch.object(svc, "_find_similar_doc", AsyncMock(return_value=existing_path)),
        patch.object(svc, "_index_doc_in_rag", AsyncMock(return_value=None)),
    ):
        rel_path, doc_ref, is_update = await svc.write_doc(
            agent_id="be-doc",
            req=WriteDocInput(
                task_id=docs_setup["task_id"],
                filename="existing.md",
                doc_type="api",
                title="New Title",
                content="# Updated",
            ),
        )
    assert is_update is True
    assert rel_path == existing_path
    # On update, doc_ref preserves created_by and adds updated_by.
    assert doc_ref.updated_by == "be-doc"


@pytest.mark.asyncio
async def test_write_doc_update_preserves_existing_metadata(
    docs_setup: dict, tmp_path: Path
) -> None:
    """If existing DocRef in task.documents, created_by/created_at preserved."""
    svc = docs_setup["svc"]
    existing_path = "backend/api/existing.md"
    # Pre-seed task.documents with an existing entry.
    result = await svc.session.execute(
        select(TaskTable).where(TaskTable.id == docs_setup["task_id"])
    )
    task = result.scalar_one()
    task.documents = [
        {
            "path": existing_path,
            "title": "Old Title",
            "doc_type": "api",
            "version": "1",
            "created_by": "be-pm",
            "created_at": "2025-01-01T00:00:00Z",
        }
    ]
    await svc.session.flush()

    with (
        patch("roboco.services.docs.DOCS_BASE_PATH", tmp_path),
        patch.object(svc, "_find_similar_doc", AsyncMock(return_value=existing_path)),
        patch.object(svc, "_index_doc_in_rag", AsyncMock(return_value=None)),
    ):
        _rel_path, doc_ref, is_update = await svc.write_doc(
            agent_id="be-doc",
            req=WriteDocInput(
                task_id=docs_setup["task_id"],
                filename="existing.md",
                doc_type="api",
                title="New Title",
                content="# Updated",
            ),
        )
    assert is_update is True
    # Original creator preserved, updater set to current agent.
    assert doc_ref.created_by == "be-pm"
    assert doc_ref.updated_by == "be-doc"


@pytest.mark.asyncio
async def test_write_doc_no_collapse_on_different_filename(
    docs_setup: dict, tmp_path: Path
) -> None:
    """#35: a similar doc with a DIFFERENT filename must not be overwritten —
    the agent named a new file, so create it instead of collapsing onto the
    similar doc's path."""
    svc = docs_setup["svc"]
    existing_path = "backend/api/existing.md"
    with (
        patch("roboco.services.docs.DOCS_BASE_PATH", tmp_path),
        patch.object(svc, "_find_similar_doc", AsyncMock(return_value=existing_path)),
        patch.object(svc, "_index_doc_in_rag", AsyncMock(return_value=None)),
    ):
        rel_path, _doc_ref, is_update = await svc.write_doc(
            agent_id="be-doc",
            req=WriteDocInput(
                task_id=docs_setup["task_id"],
                filename="other.md",
                doc_type="api",
                title="New Title",
                content="# New",
            ),
        )
    assert is_update is False
    # A new file is created at the requested filename, NOT the similar doc path.
    assert rel_path.endswith("other.md")
    assert rel_path != existing_path


@pytest.mark.asyncio
async def test_write_doc_update_path_containment_checked(
    docs_setup: dict, tmp_path: Path
) -> None:
    """#33: the RAG-returned update path is containment-checked — a ``source``
    that escapes the docs dir (``../../etc/evil.md``) is refused, not written."""
    svc = docs_setup["svc"]
    escaping = "../../etc/evil.md"
    with (
        patch("roboco.services.docs.DOCS_BASE_PATH", tmp_path),
        patch.object(svc, "_find_similar_doc", AsyncMock(return_value=escaping)),
        patch.object(svc, "_index_doc_in_rag", AsyncMock(return_value=None)),
        pytest.raises(ValidationError),
    ):
        await svc.write_doc(
            agent_id="be-doc",
            req=WriteDocInput(
                task_id=docs_setup["task_id"],
                filename="evil.md",
                doc_type="api",
                title="Title",
                content="# x",
            ),
        )


@pytest.mark.asyncio
async def test_write_doc_commit_status_skipped_when_no_branch(
    docs_setup: dict, tmp_path: Path
) -> None:
    """#34: when there is no task branch to commit onto, the doc still saves to
    /app/docs and the doc_ref carries ``commit_status='skipped'`` (not a silent
    None) so the agent knows the repo commit did not happen."""
    svc = docs_setup["svc"]
    with (
        patch("roboco.services.docs.DOCS_BASE_PATH", tmp_path),
        patch.object(svc, "_find_similar_doc", AsyncMock(return_value=None)),
        patch.object(svc, "_index_doc_in_rag", AsyncMock(return_value=None)),
    ):
        _rel, doc_ref, _is_update = await svc.write_doc(
            agent_id="be-doc",
            req=WriteDocInput(
                task_id=docs_setup["task_id"],
                filename="example.md",
                doc_type="api",
                title="Title",
                content="# Hello",
            ),
        )
    # The fixture task has no branch_name → commit is skipped, not silent.
    assert doc_ref.commit_status == "skipped"


@pytest.mark.asyncio
async def test_commit_doc_to_repo_returns_failed_on_git_error(
    docs_setup: dict,
) -> None:
    """#34: a git hiccup surfaces as ``failed`` (fail-loud), not a swallowed
    None — the agent can tell the cell PM the doc did not reach the repo."""
    svc = docs_setup["svc"]
    # Give the task a branch + project so the commit path is entered.
    result = await svc.session.execute(
        select(TaskTable).where(TaskTable.id == docs_setup["task_id"])
    )
    task = result.scalar_one()
    task.branch_name = "feature/docs"
    await svc.session.flush()

    fake_git = MagicMock()
    fake_git.get_workspace = AsyncMock(side_effect=RuntimeError("git boom"))
    with patch("roboco.services.git.get_git_service", return_value=fake_git):
        status = await svc._commit_doc_to_repo(
            "be-doc",
            WriteDocInput(
                task_id=docs_setup["task_id"],
                filename="example.md",
                doc_type="api",
                title="Title",
                content="# Hello",
            ),
            "api",
        )
    assert status == "failed"


@pytest.mark.asyncio
async def test_commit_doc_to_repo_returns_committed_on_success(
    docs_setup: dict, tmp_path: Path
) -> None:
    """#34: a successful repo commit reports ``committed``."""
    svc = docs_setup["svc"]
    result = await svc.session.execute(
        select(TaskTable).where(TaskTable.id == docs_setup["task_id"])
    )
    task = result.scalar_one()
    task.branch_name = "feature/docs"
    await svc.session.flush()

    fake_git = MagicMock()
    fake_git.get_workspace = AsyncMock(return_value=tmp_path)
    fake_git.commit = AsyncMock(return_value={"oid": "abc"})
    with (
        patch("roboco.services.git.get_git_service", return_value=fake_git),
        patch.object(svc, "_write_file", AsyncMock(return_value=None)),
    ):
        status = await svc._commit_doc_to_repo(
            str(docs_setup["agent_id"]),
            WriteDocInput(
                task_id=docs_setup["task_id"],
                filename="example.md",
                doc_type="api",
                title="Title",
                content="# Hello",
            ),
            "api",
        )
    assert status == "committed"


# ---------------------------------------------------------------------------
# _find_similar_doc
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_similar_doc_no_results(docs_setup: dict) -> None:
    """If no RAG results, returns None."""
    svc = docs_setup["svc"]
    mock_optimal = AsyncMock()
    mock_optimal.search = AsyncMock(return_value=[])
    with patch(
        "roboco.services.optimal.get_optimal_service",
        AsyncMock(return_value=mock_optimal),
    ):
        result = await svc._find_similar_doc(title="t", content="c", team="backend")
    assert result is None


@pytest.mark.asyncio
async def test_find_similar_doc_high_score_match(docs_setup: dict) -> None:
    """High-score match in same team returns the source path."""
    svc = docs_setup["svc"]
    mock_result = MagicMock()
    mock_result.score = 0.9
    mock_result.source = "backend/api/something.md"
    mock_optimal = AsyncMock()
    mock_optimal.search = AsyncMock(return_value=[mock_result])
    with patch(
        "roboco.services.optimal.get_optimal_service",
        AsyncMock(return_value=mock_optimal),
    ):
        result = await svc._find_similar_doc(
            title="t", content="c" * 1000, team="backend"
        )
    assert result == "backend/api/something.md"


@pytest.mark.asyncio
async def test_find_similar_doc_low_score_no_match(docs_setup: dict) -> None:
    """Low-score result is ignored."""
    svc = docs_setup["svc"]
    mock_result = MagicMock()
    mock_result.score = 0.5
    mock_result.source = "backend/api/something.md"
    mock_optimal = AsyncMock()
    mock_optimal.search = AsyncMock(return_value=[mock_result])
    with patch(
        "roboco.services.optimal.get_optimal_service",
        AsyncMock(return_value=mock_optimal),
    ):
        result = await svc._find_similar_doc(title="t", content="c", team="backend")
    assert result is None


@pytest.mark.asyncio
async def test_find_similar_doc_different_team_no_match(
    docs_setup: dict,
) -> None:
    """High score but different team returns None."""
    svc = docs_setup["svc"]
    mock_result = MagicMock()
    mock_result.score = 0.9
    mock_result.source = "frontend/api/something.md"
    mock_optimal = AsyncMock()
    mock_optimal.search = AsyncMock(return_value=[mock_result])
    with patch(
        "roboco.services.optimal.get_optimal_service",
        AsyncMock(return_value=mock_optimal),
    ):
        result = await svc._find_similar_doc(title="t", content="c", team="backend")
    assert result is None


@pytest.mark.asyncio
async def test_find_similar_doc_swallows_exceptions(docs_setup: dict) -> None:
    """RAG search failure returns None, doesn't raise."""
    svc = docs_setup["svc"]
    with patch(
        "roboco.services.optimal.get_optimal_service",
        AsyncMock(side_effect=RuntimeError("network down")),
    ):
        result = await svc._find_similar_doc(title="t", content="c", team="backend")
    assert result is None


# ---------------------------------------------------------------------------
# read_doc
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_doc_unauthorized(docs_setup: dict) -> None:
    svc = docs_setup["svc"]
    with pytest.raises(UnauthorizedError, match="cannot read"):
        await svc.read_doc(agent_id="ghost-agent", path="x.md")


@pytest.mark.asyncio
async def test_read_doc_head_marketing_authorized(
    docs_setup: dict, tmp_path: Path
) -> None:
    """Head of Marketing has read-only docs access (Board oversight)."""
    svc = docs_setup["svc"]
    target = tmp_path / "board" / "design" / "brand.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Brand", encoding="utf-8")
    with patch("roboco.services.docs.DOCS_BASE_PATH", tmp_path):
        content, _ = await svc.read_doc(
            agent_id="head-marketing", path="board/design/brand.md"
        )
    assert content == "# Brand"


@pytest.mark.asyncio
async def test_read_doc_path_traversal(docs_setup: dict) -> None:
    svc = docs_setup["svc"]
    with pytest.raises(ValidationError, match="cannot contain"):
        await svc.read_doc(agent_id="be-doc", path="../etc/passwd")


@pytest.mark.asyncio
async def test_read_doc_success(docs_setup: dict, tmp_path: Path) -> None:
    """Read existing file."""
    svc = docs_setup["svc"]
    target = tmp_path / "backend" / "api" / "x.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Hello", encoding="utf-8")
    with patch("roboco.services.docs.DOCS_BASE_PATH", tmp_path):
        content, size = await svc.read_doc(agent_id="be-doc", path="backend/api/x.md")
    assert content == "# Hello"
    assert size == len(b"# Hello")


@pytest.mark.asyncio
async def test_read_doc_not_found(docs_setup: dict, tmp_path: Path) -> None:
    """Read missing file → NotFoundError."""
    svc = docs_setup["svc"]
    with (
        patch("roboco.services.docs.DOCS_BASE_PATH", tmp_path),
        pytest.raises(NotFoundError),
    ):
        await svc.read_doc(agent_id="be-doc", path="ghost.md")


# ---------------------------------------------------------------------------
# list_docs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_docs_unauthorized(docs_setup: dict) -> None:
    svc = docs_setup["svc"]
    with pytest.raises(UnauthorizedError):
        await svc.list_docs(agent_id="ghost-agent")


@pytest.mark.asyncio
async def test_list_docs_head_marketing_authorized(
    docs_setup: dict, tmp_path: Path
) -> None:
    """Head of Marketing can list docs (read-only Board oversight)."""
    svc = docs_setup["svc"]
    with patch("roboco.services.docs.DOCS_BASE_PATH", tmp_path):
        docs = await svc.list_docs(agent_id="head-marketing")
    assert docs == []


@pytest.mark.asyncio
async def test_list_docs_by_task_id(docs_setup: dict) -> None:
    """Pass task_id, list from task.documents."""
    svc = docs_setup["svc"]
    # Pre-seed task.documents.
    result = await svc.session.execute(
        select(TaskTable).where(TaskTable.id == docs_setup["task_id"])
    )
    task = result.scalar_one()
    task.documents = [
        {
            "path": "backend/api/x.md",
            "title": "X",
            "doc_type": "api",
            "version": "1",
        }
    ]
    await svc.session.flush()
    docs = await svc.list_docs(agent_id="be-doc", task_id=docs_setup["task_id"])
    assert len(docs) == 1
    assert docs[0].path == "backend/api/x.md"


@pytest.mark.asyncio
async def test_list_docs_by_task_id_not_found(docs_setup: dict) -> None:
    svc = docs_setup["svc"]
    with pytest.raises(NotFoundError):
        await svc.list_docs(agent_id="be-doc", task_id=uuid4())


@pytest.mark.asyncio
async def test_list_docs_unknown_agent_team_returns_empty(
    docs_setup: dict,
) -> None:
    """Agent with no team but in READ_ROLES returns empty list."""
    svc = docs_setup["svc"]
    # Patch get_agent_team to return None while keeping role permissions.
    with patch("roboco.services.docs.get_agent_team", return_value=None):
        docs = await svc.list_docs(agent_id="be-doc")
    assert docs == []


@pytest.mark.asyncio
async def test_list_docs_filesystem_scan(docs_setup: dict, tmp_path: Path) -> None:
    """Scan filesystem for team docs."""
    svc = docs_setup["svc"]
    backend_dir = tmp_path / "backend" / "api"
    backend_dir.mkdir(parents=True)
    (backend_dir / "endpoint.md").write_text("# x", encoding="utf-8")
    (tmp_path / "backend" / "README.md").write_text("# r", encoding="utf-8")
    with patch("roboco.services.docs.DOCS_BASE_PATH", tmp_path):
        docs = await svc.list_docs(agent_id="be-doc")
    paths = {d.path for d in docs}
    assert "backend/api/endpoint.md" in paths
    assert "backend/README.md" in paths


@pytest.mark.asyncio
async def test_list_docs_filesystem_no_dir_returns_empty(
    docs_setup: dict, tmp_path: Path
) -> None:
    """No team folder → empty list."""
    svc = docs_setup["svc"]
    with patch("roboco.services.docs.DOCS_BASE_PATH", tmp_path):
        docs = await svc.list_docs(agent_id="be-doc")
    assert docs == []


# ---------------------------------------------------------------------------
# delete_doc
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_doc_unauthorized(docs_setup: dict) -> None:
    svc = docs_setup["svc"]
    with pytest.raises(UnauthorizedError, match="cannot delete"):
        await svc.delete_doc(agent_id="be-dev-1", path="backend/api/x.md")


@pytest.mark.asyncio
async def test_delete_doc_path_traversal(docs_setup: dict) -> None:
    svc = docs_setup["svc"]
    with pytest.raises(ValidationError):
        await svc.delete_doc(agent_id="be-doc", path="../etc/passwd")


@pytest.mark.asyncio
async def test_delete_doc_not_found(docs_setup: dict, tmp_path: Path) -> None:
    svc = docs_setup["svc"]
    with (
        patch("roboco.services.docs.DOCS_BASE_PATH", tmp_path),
        pytest.raises(NotFoundError),
    ):
        await svc.delete_doc(agent_id="be-doc", path="missing.md")


@pytest.mark.asyncio
async def test_delete_doc_success(docs_setup: dict, tmp_path: Path) -> None:
    svc = docs_setup["svc"]
    target = tmp_path / "backend" / "api" / "x.md"
    target.parent.mkdir(parents=True)
    target.write_text("# x", encoding="utf-8")
    with patch("roboco.services.docs.DOCS_BASE_PATH", tmp_path):
        result = await svc.delete_doc(agent_id="be-doc", path="backend/api/x.md")
    assert result is True
    assert not target.exists()


# ---------------------------------------------------------------------------
# _infer_doc_type
# ---------------------------------------------------------------------------


def test_infer_doc_type_with_subfolder(docs_setup: dict) -> None:
    svc = docs_setup["svc"]
    assert svc._infer_doc_type("backend/api/x.md") == "api"


def test_infer_doc_type_without_subfolder(docs_setup: dict) -> None:
    """Path with only team/file (2 parts) → readme inferred."""
    svc = docs_setup["svc"]
    # 2 parts = team/file → readme.
    assert svc._infer_doc_type("backend/x.md") == "readme"


def test_infer_doc_type_too_short(docs_setup: dict) -> None:
    """Single part → readme default."""
    svc = docs_setup["svc"]
    assert svc._infer_doc_type("x.md") == "readme"


# ---------------------------------------------------------------------------
# _add_doc_to_task — task missing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_doc_to_task_missing_task(docs_setup: dict) -> None:
    svc = docs_setup["svc"]
    doc_ref = DocRef(path="x.md", title="t", doc_type="readme")
    with pytest.raises(NotFoundError):
        await svc._add_doc_to_task(uuid4(), doc_ref)


@pytest.mark.asyncio
async def test_add_doc_to_task_existing_path_is_updated(
    docs_setup: dict,
) -> None:
    """If path already exists in task.documents, it gets updated."""
    svc = docs_setup["svc"]
    result = await svc.session.execute(
        select(TaskTable).where(TaskTable.id == docs_setup["task_id"])
    )
    task = result.scalar_one()
    task.documents = [
        {
            "path": "backend/api/x.md",
            "title": "Old",
            "doc_type": "api",
            "version": "1",
        }
    ]
    await svc.session.flush()

    new_ref = DocRef(
        path="backend/api/x.md",
        title="New",
        doc_type="api",
    )
    await svc._add_doc_to_task(docs_setup["task_id"], new_ref)
    # Only one entry remains, with updated title.
    result = await svc.session.execute(
        select(TaskTable).where(TaskTable.id == docs_setup["task_id"])
    )
    refreshed = result.scalar_one()
    assert len(refreshed.documents) == 1
    assert refreshed.documents[0]["title"] == "New"


# ---------------------------------------------------------------------------
# _index_doc_in_rag — failure swallowed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_index_doc_in_rag_swallows_exception(
    docs_setup: dict, tmp_path: Path
) -> None:
    svc = docs_setup["svc"]
    with patch(
        "roboco.services.optimal.get_optimal_service",
        AsyncMock(side_effect=RuntimeError("rag down")),
    ):
        # No exception raised.
        await svc._index_doc_in_rag(tmp_path / "x.md")


@pytest.mark.asyncio
async def test_index_doc_in_rag_success(docs_setup: dict, tmp_path: Path) -> None:
    """Successful index call."""
    svc = docs_setup["svc"]
    mock_optimal = AsyncMock()
    mock_optimal.index_documentation = AsyncMock(return_value=None)
    with patch(
        "roboco.services.optimal.get_optimal_service",
        AsyncMock(return_value=mock_optimal),
    ):
        await svc._index_doc_in_rag(tmp_path / "x.md")
    mock_optimal.index_documentation.assert_awaited_once()


# ---------------------------------------------------------------------------
# _get_existing_doc_ref
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_existing_doc_ref_none_when_task_missing(
    docs_setup: dict,
) -> None:
    svc = docs_setup["svc"]
    result = await svc._get_existing_doc_ref(uuid4(), "x.md")
    assert result is None


@pytest.mark.asyncio
async def test_get_existing_doc_ref_none_when_path_missing(
    docs_setup: dict,
) -> None:
    svc = docs_setup["svc"]
    # No documents on task.
    result = await svc._get_existing_doc_ref(docs_setup["task_id"], "x.md")
    assert result is None


@pytest.mark.asyncio
async def test_get_existing_doc_ref_returns_match(docs_setup: dict) -> None:
    svc = docs_setup["svc"]
    result = await svc.session.execute(
        select(TaskTable).where(TaskTable.id == docs_setup["task_id"])
    )
    task = result.scalar_one()
    task.documents = [
        {
            "path": "backend/api/x.md",
            "title": "T",
            "doc_type": "api",
            "version": "1",
        }
    ]
    await svc.session.flush()
    found = await svc._get_existing_doc_ref(docs_setup["task_id"], "backend/api/x.md")
    assert found is not None
    assert found.path == "backend/api/x.md"


@pytest.mark.asyncio
async def test_get_existing_doc_ref_no_match_among_documents(
    docs_setup: dict,
) -> None:
    """Documents present but path not in any of them → None."""
    svc = docs_setup["svc"]
    result = await svc.session.execute(
        select(TaskTable).where(TaskTable.id == docs_setup["task_id"])
    )
    task = result.scalar_one()
    task.documents = [
        {
            "path": "backend/api/other.md",
            "title": "T",
            "doc_type": "api",
            "version": "1",
        }
    ]
    await svc.session.flush()
    found = await svc._get_existing_doc_ref(docs_setup["task_id"], "backend/api/x.md")
    assert found is None


# ---------------------------------------------------------------------------
# _list_docs_for_team — filesystem scan with shallow paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_docs_for_team_shallow_path_infers_readme(
    docs_setup: dict, tmp_path: Path
) -> None:
    """A .md file directly under the team folder (1 part after team) → readme.

    The filesystem scan returns the relative path including the team prefix
    (e.g., 'backend/x.md'). Splitting that gives 2 parts — meeting the
    `>= _MIN_PATH_PARTS_FOR_SUBFOLDER` check but `not has_subfolder` (since
    `len(parts) > _MIN_PATH_PARTS_FOR_SUBFOLDER` is False with exactly 2).
    The else-branch on line 604 runs when len(parts) < 2, which only happens
    for a path with no separators — `_list_docs_for_team` always produces at
    least `team/file`, so we patch DOCS_BASE_PATH to be the team dir itself.
    """
    svc = docs_setup["svc"]
    # Place the docs root AT the team level so rel_path is just "x.md".
    backend_root = tmp_path / "backend"
    backend_root.mkdir()
    (backend_root / "x.md").write_text("# x", encoding="utf-8")
    # DOCS_BASE_PATH set to backend_root means rel_path = 'x.md' (1 part).
    with (
        patch("roboco.services.docs.DOCS_BASE_PATH", backend_root),
        patch("roboco.services.docs.TEAM_PATHS", {"backend": ""}),
    ):
        docs = await svc._list_docs_for_team("backend")
    assert any(d.doc_type == "readme" for d in docs)


@pytest.mark.asyncio
async def test_commit_doc_to_repo_writes_into_workspace_and_commits(
    docs_setup: dict,
    db_session: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A doc write also commits the file into the project repo on the task branch."""
    svc = docs_setup["svc"]
    task_id = docs_setup["task_id"]
    agent_uuid = docs_setup["agent_id"]

    task = (
        await db_session.execute(select(TaskTable).where(TaskTable.id == task_id))
    ).scalar_one()
    task.branch_name = "feature/backend/ABC12345"
    await db_session.flush()

    mock_git = MagicMock()
    mock_git.get_workspace = AsyncMock(return_value=tmp_path)
    mock_git.commit = AsyncMock(return_value={"sha": "deadbeef"})
    monkeypatch.setattr(
        "roboco.services.git.get_git_service", lambda _session: mock_git
    )

    req = WriteDocInput(
        task_id=task_id,
        filename="guide.md",
        doc_type="api",
        title="API Guide",
        content="# API Guide\n",
    )
    await svc._commit_doc_to_repo(str(agent_uuid), req, "api")

    # The doc landed in the workspace repo under docs/...
    assert (tmp_path / "docs" / "api" / "guide.md").read_text() == "# API Guide\n"
    # ...and was committed onto the task branch.
    mock_git.commit.assert_awaited_once()
    kwargs = mock_git.commit.await_args.kwargs
    assert kwargs["branch_name"] == "feature/backend/ABC12345"
    assert kwargs["files"] == ["docs/api/guide.md"]
    assert kwargs["task_id"] == task_id


@pytest.mark.asyncio
async def test_commit_doc_to_repo_skips_without_task_branch(
    docs_setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No task branch yet → best-effort no-op (no git commit, no raise)."""
    svc = docs_setup["svc"]
    mock_git = MagicMock()
    mock_git.commit = AsyncMock()
    monkeypatch.setattr(
        "roboco.services.git.get_git_service", lambda _session: mock_git
    )
    req = WriteDocInput(
        task_id=docs_setup["task_id"],
        filename="x.md",
        doc_type="api",
        title="X",
        content="x",
    )
    await svc._commit_doc_to_repo(str(docs_setup["agent_id"]), req, "api")
    mock_git.commit.assert_not_awaited()
