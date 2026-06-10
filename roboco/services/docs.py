"""
Documentation Service

Handles documentation file management for agents:
- Write documentation files with automatic team/path resolution
- Track documents in task.documents for database traceability
- Auto-index in RAG for searchability
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar
from uuid import UUID

from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from roboco.agents_config import get_agent_role, get_agent_team
from roboco.db.tables import TaskTable
from roboco.models.task import DocRef
from roboco.services.base import (
    BaseService,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)


@dataclass(frozen=True)
class WriteDocInput:
    """Primitive write-doc fields from the API layer.

    Route-layer DTO that bundles the raw inputs so the service method
    signature stays pure of api.schemas. `doc_type` is a plain string;
    the Pydantic StrEnum validation happens at the HTTP boundary, and
    the service re-validates against TYPE_SUBFOLDERS.
    """

    task_id: UUID
    filename: str
    doc_type: str
    title: str
    content: str


# =============================================================================
# CONSTANTS
# =============================================================================

# Base path for documentation in container
DOCS_BASE_PATH = Path("/app/docs")

# Team to folder mapping
TEAM_PATHS: dict[str, str] = {
    "backend": "backend",
    "frontend": "frontend",
    "ux_ui": "ux_ui",
}

# Doc type to subfolder mapping
TYPE_SUBFOLDERS: dict[str, str] = {
    "api": "api",
    "qa": "qa",
    "guide": "guides",
    "readme": "",  # Root of team folder
    "changelog": "",
    "architecture": "architecture",
    "design": "design",
}

# Roles that can write documentation
WRITE_ROLES: frozenset[str] = frozenset({"documenter", "cell_pm"})

# Roles that can read documentation. The Board (head_marketing) gets
# read-only access for oversight — the spawn manifest mounts the roboco-docs
# MCP for it, so this set must agree or list/read 403 against a tool the
# agent was handed.
READ_ROLES: frozenset[str] = frozenset(
    {
        "documenter",
        "cell_pm",
        "main_pm",
        "developer",
        "qa",
        "auditor",
        "ceo",
        "head_marketing",
    }
)

# Path parsing constants
_MIN_PATH_PARTS_FOR_SUBFOLDER = 2
_SUBFOLDER_INDEX = 1

# RAG similarity threshold for doc deduplication
# Above this score, we update existing doc instead of creating new
_SIMILARITY_THRESHOLD = 0.75

# Max chars of content to use for similarity search
_CONTENT_SUMMARY_LENGTH = 500


def _coerce_doc_ref(d: object) -> DocRef:
    """Build a DocRef from a stored ``Task.documents`` element.

    Canonical rows are dicts (``DocRef.model_dump()``). Defensive
    against legacy/corrupted rows: a bare path string is wrapped
    instead of exploding ``DocRef(**str)`` and 500-ing the endpoint.
    """
    if isinstance(d, DocRef):
        return d
    if isinstance(d, str):
        return DocRef(path=d, title=Path(d).name, doc_type="doc")
    if isinstance(d, dict):
        return DocRef.model_validate(d)
    raise TypeError(f"unsupported Task.documents element: {type(d).__name__}")


# =============================================================================
# SERVICE
# =============================================================================


class DocsService(BaseService):
    """Service for documentation file management."""

    service_name: ClassVar[str] = "docs"

    async def write_doc(
        self,
        agent_id: str,
        req: WriteDocInput,
    ) -> tuple[str, DocRef, bool]:
        """
        Write or update a documentation file with RAG-based deduplication.

        Before creating a new doc, searches RAG for similar existing docs.
        If a high-similarity match is found, updates that doc instead.

        Args:
            agent_id: Agent slug or UUID writing the doc
            req: Write request with task_id, filename, doc_type, title, content

        Returns:
            Tuple of (relative_path, DocRef, is_update)
            - is_update: True if existing doc was updated, False if new created

        Raises:
            ValidationError: If team unknown or invalid doc_type
            UnauthorizedError: If agent cannot write docs
            NotFoundError: If task not found
        """
        # 1. Validate agent and get team
        team = get_agent_team(agent_id)
        if not team:
            raise ValidationError(
                f"Unknown agent team for {agent_id}",
                field="agent_id",
            )

        role = get_agent_role(agent_id)
        if role not in WRITE_ROLES:
            raise UnauthorizedError(
                action="write_doc",
                reason=(
                    f"Role '{role}' cannot write documentation. "
                    "Only documenters and cell PMs allowed."
                ),
            )

        doc_type = req.doc_type

        # 2. Validate doc_type
        if doc_type not in TYPE_SUBFOLDERS:
            valid_types = list(TYPE_SUBFOLDERS.keys())
            raise ValidationError(
                f"Unknown doc_type: {doc_type}. Valid types: {valid_types}",
                field="doc_type",
            )

        # 3. Validate filename (no path traversal)
        if "/" in req.filename or "\\" in req.filename or ".." in req.filename:
            raise ValidationError(
                "Filename cannot contain path separators or '..'",
                field="filename",
            )

        # 4. Search RAG for similar existing documentation (by content, not just title)
        existing_path = await self._find_similar_doc(req.title, req.content, team)

        if existing_path:
            # UPDATE existing doc instead of creating new
            return await self._update_existing_doc(
                agent_id=agent_id,
                existing_path=existing_path,
                req=req,
                doc_type=doc_type,
            )

        # 5. No similar doc found - create new
        return await self._create_new_doc(
            agent_id=agent_id,
            team=team,
            req=req,
            doc_type=doc_type,
        )

    async def _find_similar_doc(
        self,
        title: str,
        content: str,
        team: str,
    ) -> str | None:
        """
        Search RAG for existing doc with similar content.

        Uses content (not just title) to find semantically similar documents.
        Returns the path of the similar doc if found above threshold,
        None otherwise.
        """
        try:
            from roboco.models.optimal import IndexType, QueryContext
            from roboco.services.optimal import get_optimal_service

            optimal = await get_optimal_service()

            # Build search query from title + content summary
            content_summary = (
                content[:_CONTENT_SUMMARY_LENGTH]
                if len(content) > _CONTENT_SUMMARY_LENGTH
                else content
            )
            search_query = f"{title}\n\n{content_summary}"

            # Search documentation index by content similarity
            context = QueryContext(index_types=[IndexType.DOCUMENTATION])
            results = await optimal.search(
                query=search_query,
                context=context,
                top_k=5,
            )

            if not results:
                return None

            # Check for high-similarity match in same team
            for result in results:
                if result.score >= _SIMILARITY_THRESHOLD:
                    # Check if it's in the same team's docs
                    source = result.source or ""
                    if source.startswith(f"{team}/") or f"/{team}/" in source:
                        self.log.info(
                            "Found similar existing doc by content",
                            title=title,
                            existing_path=source,
                            score=result.score,
                        )
                        return source

            return None

        except Exception as e:
            # RAG search failure shouldn't block doc creation
            self.log.warning(
                "RAG search for similar docs failed",
                title=title,
                error=str(e),
            )
            return None

    async def _create_new_doc(
        self,
        agent_id: str,
        team: str,
        req: WriteDocInput,
        doc_type: str,
    ) -> tuple[str, DocRef, bool]:
        """Create a new documentation file."""
        # Build relative path: {team}/{type_subfolder}/{filename}
        team_path = TEAM_PATHS.get(team, team)
        subfolder = TYPE_SUBFOLDERS[doc_type]
        if subfolder:
            rel_path = f"{team_path}/{subfolder}/{req.filename}"
        else:
            rel_path = f"{team_path}/{req.filename}"

        full_path = DOCS_BASE_PATH / rel_path

        self.log.info(
            "Creating new documentation",
            agent_id=agent_id,
            task_id=str(req.task_id),
            path=rel_path,
            doc_type=doc_type,
        )

        # Write file
        await self._write_file(full_path, req.content)

        # Create DocRef
        doc_ref = DocRef(
            path=rel_path,
            title=req.title,
            doc_type=doc_type,
            created_by=agent_id,
            created_at=datetime.now(UTC).isoformat(),
        )

        # Add to task.documents
        await self._add_doc_to_task(req.task_id, doc_ref)

        # Index in RAG
        await self._index_doc_in_rag(full_path)

        return rel_path, doc_ref, False  # is_update=False

    async def _update_existing_doc(
        self,
        agent_id: str,
        existing_path: str,
        req: WriteDocInput,
        doc_type: str,
    ) -> tuple[str, DocRef, bool]:
        """Update an existing documentation file."""
        full_path = DOCS_BASE_PATH / existing_path

        self.log.info(
            "Updating existing documentation (RAG dedup)",
            agent_id=agent_id,
            task_id=str(req.task_id),
            existing_path=existing_path,
            new_title=req.title,
        )

        # Write updated content to existing path
        await self._write_file(full_path, req.content)

        # Get existing DocRef to preserve created_by/created_at
        existing_ref = await self._get_existing_doc_ref(req.task_id, existing_path)

        now = datetime.now(UTC).isoformat()

        # Create updated DocRef - preserve original creation info, set update info
        doc_ref = DocRef(
            path=existing_path,
            title=req.title,
            doc_type=doc_type,
            created_by=existing_ref.created_by if existing_ref else agent_id,
            created_at=existing_ref.created_at if existing_ref else now,
            updated_by=agent_id,
            updated_at=now,
        )

        # Link to new task (doc evolves across tasks)
        await self._add_doc_to_task(req.task_id, doc_ref)

        # Re-index in RAG
        await self._index_doc_in_rag(full_path)

        return existing_path, doc_ref, True  # is_update=True

    async def read_doc(
        self,
        agent_id: str,
        path: str,
    ) -> tuple[str, int]:
        """
        Read a documentation file.

        Args:
            agent_id: Agent slug or UUID reading the doc
            path: Normalized path (e.g., "backend/api/endpoints.md")

        Returns:
            Tuple of (content, size_bytes)

        Raises:
            UnauthorizedError: If agent cannot read docs
            NotFoundError: If file not found
        """
        # 1. Check read permission
        role = get_agent_role(agent_id)
        if role not in READ_ROLES:
            raise UnauthorizedError(
                action="read_doc",
                reason=f"Role '{role}' cannot read documentation.",
            )

        # 2. Validate path (no traversal)
        if ".." in path:
            raise ValidationError(
                "Path cannot contain '..'",
                field="path",
            )

        # 3. Build full path
        full_path = DOCS_BASE_PATH / path

        # 4. Read file
        content = await self._read_file(full_path)
        size_bytes = len(content.encode("utf-8"))

        return content, size_bytes

    async def list_docs(
        self,
        agent_id: str,
        task_id: UUID | None = None,
    ) -> list[DocRef]:
        """
        List documentation files.

        Args:
            agent_id: Agent slug or UUID
            task_id: Optional task to filter by

        Returns:
            List of DocRef objects
        """
        # Check read permission
        role = get_agent_role(agent_id)
        if role not in READ_ROLES:
            raise UnauthorizedError(
                action="list_docs",
                reason=f"Role '{role}' cannot list documentation.",
            )

        if task_id:
            # Get docs for specific task
            result = await self.session.execute(
                select(TaskTable).where(TaskTable.id == task_id)
            )
            task = result.scalar_one_or_none()
            if not task:
                raise NotFoundError("Task", str(task_id))
            return [_coerce_doc_ref(d) for d in (task.documents or [])]
        else:
            # Get agent's team and list files from filesystem
            team = get_agent_team(agent_id)
            if not team:
                return []
            return await self._list_docs_for_team(team)

    async def delete_doc(
        self,
        agent_id: str,
        path: str,
    ) -> bool:
        """
        Delete a documentation file.

        Args:
            agent_id: Agent slug or UUID
            path: Normalized path (e.g., "backend/api/endpoints.md")

        Returns:
            True if deleted

        Raises:
            UnauthorizedError: If agent cannot write docs
            NotFoundError: If file not found
        """
        # 1. Check write permission
        role = get_agent_role(agent_id)
        if role not in WRITE_ROLES:
            raise UnauthorizedError(
                action="delete_doc",
                reason=(
                    f"Role '{role}' cannot delete documentation. "
                    "Only documenters and cell PMs allowed."
                ),
            )

        # 2. Validate path
        if ".." in path:
            raise ValidationError("Path cannot contain '..'", field="path")

        # 3. Build full path and verify file exists
        full_path = DOCS_BASE_PATH / path
        if not full_path.exists():
            raise NotFoundError("Documentation file", path)

        # 4. Delete file
        await self._delete_file(full_path)

        self.log.info("Documentation deleted", agent_id=agent_id, path=path)
        return True

    def _infer_doc_type(self, path: str) -> str:
        """Infer doc_type from path."""
        parts = path.split("/")
        if len(parts) >= _MIN_PATH_PARTS_FOR_SUBFOLDER:
            has_subfolder = len(parts) > _MIN_PATH_PARTS_FOR_SUBFOLDER
            subfolder = parts[_SUBFOLDER_INDEX] if has_subfolder else ""
            return next(
                (k for k, v in TYPE_SUBFOLDERS.items() if v == subfolder),
                "readme",
            )
        return "readme"

    # =========================================================================
    # INTERNAL METHODS
    # =========================================================================

    async def _write_file(self, path: Path, content: str) -> None:
        """Write file using thread pool."""

        def _do_write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        await asyncio.to_thread(_do_write)
        self.log.debug("File written", path=str(path), size=len(content))

    async def _read_file(self, path: Path) -> str:
        """Read file using thread pool."""

        def _do_read() -> str:
            if not path.exists():
                raise NotFoundError("Documentation file", str(path))
            return path.read_text(encoding="utf-8")

        return await asyncio.to_thread(_do_read)

    async def _delete_file(self, path: Path) -> None:
        """Delete file using thread pool."""

        def _do_delete() -> None:
            if path.exists():
                path.unlink()

        await asyncio.to_thread(_do_delete)
        self.log.debug("File deleted", path=str(path))

    async def _get_existing_doc_ref(self, task_id: UUID, path: str) -> DocRef | None:
        """Get existing DocRef from task.documents by path."""
        result = await self.session.execute(
            select(TaskTable).where(TaskTable.id == task_id)
        )
        task = result.scalar_one_or_none()
        if not task or not task.documents:
            return None

        for doc in task.documents:
            ref = _coerce_doc_ref(doc)
            if ref.path == path:
                return ref
        return None

    async def _add_doc_to_task(self, task_id: UUID, doc_ref: DocRef) -> None:
        """Add DocRef to task.documents."""
        result = await self.session.execute(
            select(TaskTable).where(TaskTable.id == task_id)
        )
        task = result.scalar_one_or_none()
        if not task:
            raise NotFoundError("Task", str(task_id))

        # Append to documents list
        docs = list(task.documents or [])

        # Check if doc with same path exists (update) or new (create)
        existing_idx = next(
            (i for i, d in enumerate(docs) if d.get("path") == doc_ref.path),
            None,
        )

        if existing_idx is not None:
            docs[existing_idx] = doc_ref.model_dump()
            status = "updated"
        else:
            docs.append(doc_ref.model_dump())
            status = "created"

        task.documents = docs
        self.log.info(
            f"DocRef {status} in task",
            task_id=str(task_id),
            path=doc_ref.path,
        )

    async def _index_doc_in_rag(self, path: Path) -> None:
        """Index document in RAG. Failures are logged but don't break flow."""
        try:
            # Import here to avoid circular dependency
            from roboco.services.optimal import get_optimal_service

            optimal = await get_optimal_service()
            await optimal.index_documentation(sources=[str(path)])
            self.log.debug("Document indexed in RAG", path=str(path))
        except Exception as e:
            # Log but don't fail - RAG indexing is nice-to-have
            self.log.warning(
                "Failed to index document in RAG",
                path=str(path),
                error=str(e),
            )

    async def _list_docs_for_team(self, team: str) -> list[DocRef]:
        """List documentation files for a team from filesystem."""
        team_path = TEAM_PATHS.get(team, team)
        base = DOCS_BASE_PATH / team_path

        def _scan_docs() -> list[DocRef]:
            docs: list[DocRef] = []
            if not base.exists():
                return docs

            for md_file in base.rglob("*.md"):
                rel_path = str(md_file.relative_to(DOCS_BASE_PATH))
                # Infer doc_type from path
                parts = rel_path.split("/")
                if len(parts) >= _MIN_PATH_PARTS_FOR_SUBFOLDER:
                    has_subfolder = len(parts) > _MIN_PATH_PARTS_FOR_SUBFOLDER
                    subfolder = parts[_SUBFOLDER_INDEX] if has_subfolder else ""
                    doc_type = next(
                        (k for k, v in TYPE_SUBFOLDERS.items() if v == subfolder),
                        "readme",
                    )
                else:
                    doc_type = "readme"

                docs.append(
                    DocRef(
                        path=rel_path,
                        title=md_file.stem.replace("_", " ").replace("-", " ").title(),
                        doc_type=doc_type,
                    )
                )
            return docs

        return await asyncio.to_thread(_scan_docs)


# =============================================================================
# FACTORY
# =============================================================================


def get_docs_service(session: "AsyncSession") -> DocsService:
    """Factory function for DocsService."""
    return DocsService(session)
