"""
Optimal API Service (Refactored with Plugin Architecture)

Knowledge base, RAG queries, and prompt optimization using piragi.
This service provides semantic search across code, documentation,
conversations, journal entries, errors, standards, decisions, reviews, and learnings.

The service uses a plugin-based architecture where each index type is handled
by a specialized plugin that implements the BaseIndexPlugin interface.
"""

from pathlib import Path
from typing import Any

import structlog

from roboco.models.optimal import (
    IndexConversationParams,
    IndexDecisionParams,
    IndexErrorParams,
    IndexJournalEntryParams,
    IndexReviewParams,
    IndexStandardParams,
    IndexType,
    QueryContext,
    RAGResponse,
    SearchResult,
)
from roboco.services.optimal_brain.indexes import (
    BaseIndexPlugin,
    CodeIndexPlugin,
    ConversationsIndexPlugin,
    DecisionsIndexPlugin,
    DocsIndexPlugin,
    ErrorsIndexPlugin,
    JournalsIndexPlugin,
    LearningsIndexPlugin,
    ReviewsIndexPlugin,
    StandardsIndexPlugin,
)
from roboco.services.optimal_brain.indexes.learnings import (
    RecordLearningParams as LearningParams,
)
from roboco.services.optimal_brain.indexes.reviews import (
    RecordReviewParams as ReviewParams,
)

logger = structlog.get_logger()


# Plugin registry mapping IndexType to plugin class
PLUGIN_REGISTRY: dict[IndexType, type[BaseIndexPlugin]] = {
    IndexType.CODE: CodeIndexPlugin,
    IndexType.DOCUMENTATION: DocsIndexPlugin,
    IndexType.CONVERSATIONS: ConversationsIndexPlugin,
    IndexType.JOURNALS: JournalsIndexPlugin,
    IndexType.ERRORS: ErrorsIndexPlugin,
    IndexType.STANDARDS: StandardsIndexPlugin,
    IndexType.DECISIONS: DecisionsIndexPlugin,
    IndexType.REVIEWS: ReviewsIndexPlugin,
    IndexType.LEARNINGS: LearningsIndexPlugin,
}


class OptimalService:
    """
    Service for knowledge base operations and RAG queries.

    Uses a plugin-based architecture with piragi and PostgreSQL/pgvector
    for vector storage. Manages multiple indexes for different content types:

    Existing:
    - Code: Repositories, functions, classes
    - Documentation: READMEs, API docs, guides
    - Conversations: Extracted messages from agent streams
    - Journals: Agent journal entries

    New (Optimal Brain):
    - Errors: Error patterns and solutions
    - Standards: Coding standards, security policies, workflow rules
    - Decisions: Architectural and design decisions
    - Reviews: Code review feedback
    - Learnings: Cross-agent learnings
    """

    def __init__(self) -> None:
        self._initialized = False
        self._plugins: dict[IndexType, BaseIndexPlugin] = {}
        self._prompt_templates: dict[str, dict[str, Any]] = {}

    async def initialize(self) -> None:
        """Initialize all knowledge base indexes."""
        if self._initialized:
            return

        logger.info("Initializing OptimalService with plugin architecture")

        # Create and initialize plugins for each index type
        for index_type, plugin_class in PLUGIN_REGISTRY.items():
            plugin = plugin_class()
            await plugin.initialize()
            self._plugins[index_type] = plugin
            logger.info(f"Initialized {index_type.value} plugin")

        self._initialized = True
        logger.info("OptimalService initialization complete")

        # Auto-index code and documentation on startup
        await self._auto_index_on_startup()

    async def _auto_index_on_startup(self) -> None:
        """
        Auto-index code and documentation on startup.

        Indexes:
        - /roboco/ - Source code files
        - /docs/standards/ - Coding, security, workflow standards
        - /docs/workflows/ - Agent workflow documentation

        This ensures agents can search for code, standards, and workflows
        immediately after startup.
        """
        await self._auto_index_code()
        await self._auto_index_docs()

    async def _auto_index_code(self) -> None:
        """Auto-index source code files on startup."""
        # Find the roboco source directory (the Python package, not the repo root)
        # We want to index roboco/ package, NOT the entire repo (which has .venv)
        possible_code_roots = [
            Path("/app/roboco"),  # Docker: /app is repo root, roboco/ is package
            Path(__file__).parent.parent,  # Local: optimal.py -> services -> roboco
            Path.cwd() / "roboco",  # Local: cwd/roboco
        ]

        code_root = None
        for path in possible_code_roots:
            # Check for __init__.py to confirm it's a Python package (not repo root)
            init_file = path / "__init__.py"
            if path.exists() and path.is_dir() and init_file.exists():
                code_root = path
                logger.debug(
                    "Found code package directory",
                    path=str(path),
                )
                break

        if code_root is None:
            logger.warning(
                "Code directory not found",
                searched_paths=[str(p) for p in possible_code_roots],
            )
            return

        # Check if code index is empty
        code_plugin = self._get_plugin(IndexType.CODE)
        code_count = await code_plugin.count()

        if code_count > 0:
            logger.info(
                "Code index already populated",
                chunk_count=code_count,
                skipping=True,
            )
            return

        logger.info(
            "Auto-indexing source code",
            directory=str(code_root),
        )

        try:
            count = await self.index_code([str(code_root)], project="roboco")
            logger.info("Code auto-indexing complete", files_indexed=count)
        except Exception as e:
            logger.warning("Code auto-indexing failed", error=str(e))

    async def _auto_index_docs(self) -> None:
        """
        Auto-index documentation directories on startup.

        Indexes:
        - /docs/standards/ - Coding, security, workflow standards
        - /docs/workflows/ - Agent workflow documentation
        """
        # Find the docs directory relative to the project root
        possible_docs_roots = [
            Path("/app/docs"),  # Docker absolute path
            Path(__file__).parent.parent.parent / "docs",  # roboco/docs (local)
            Path.cwd() / "docs",  # Current working directory
        ]

        docs_root = None
        for path in possible_docs_roots:
            if path.exists() and path.is_dir():
                docs_root = path
                break

        if docs_root is None:
            logger.warning(
                "Docs directory not found",
                searched_paths=[str(p) for p in possible_docs_roots],
            )
            return

        # Directories to auto-index (relative to docs root)
        auto_index_dirs = ["standards", "workflows"]

        for subdir in auto_index_dirs:
            target_dir = docs_root / subdir
            if not target_dir.exists():
                logger.debug(f"Auto-index directory not found: {target_dir}")
                continue

            await self._index_docs_directory(target_dir, subdir)

    async def _index_docs_directory(self, directory: Path, name: str) -> None:
        """Index all markdown files in a documentation directory."""
        md_files = list(directory.rglob("*.md"))
        if not md_files:
            logger.info(f"No files found to index in {name}/", path=str(directory))
            return

        logger.info(
            f"Auto-indexing {name} files",
            directory=str(directory),
            file_count=len(md_files),
        )

        # Index each file
        total_indexed = 0
        for md_file in md_files:
            try:
                if name == "standards":
                    count = await self.index_standards_file(str(md_file))
                    total_indexed += count
                else:
                    # For workflows, index as documentation
                    await self.index_documentation([str(md_file)])
                    total_indexed += 1

                logger.debug(
                    f"Indexed {name} file",
                    file=str(md_file),
                    items_indexed=count if name == "standards" else 1,
                )
            except Exception as e:
                logger.warning(
                    f"Failed to index {name} file",
                    file=str(md_file),
                    error=str(e),
                )

        logger.info(
            f"{name.capitalize()} auto-indexing complete",
            files_processed=len(md_files),
            total_indexed=total_indexed,
        )

    async def close(self) -> None:
        """Cleanup resources."""
        for plugin in self._plugins.values():
            await plugin.close()
        self._plugins.clear()
        self._initialized = False

        # Close shared embedder
        from roboco.services.optimal_brain.shared_embedder import close_shared_embedder

        await close_shared_embedder()
        logger.info("OptimalService closed")

    def _get_plugin(self, index_type: IndexType) -> BaseIndexPlugin:
        """Get the plugin for an index type."""
        if not self._initialized:
            raise RuntimeError(
                "OptimalService not initialized. Call initialize() first."
            )
        return self._plugins[index_type]

    # =========================================================================
    # INDEXING OPERATIONS (Existing - Backwards Compatible)
    # =========================================================================

    async def index_code(
        self,
        sources: list[str],
        project: str | None = None,
    ) -> int:
        """Index code files/directories and track in database."""
        plugin = self._get_plugin(IndexType.CODE)
        if isinstance(plugin, CodeIndexPlugin):
            count, indexed_files = await plugin.index_sources(sources, project)

            # Batch track all indexed files using repository
            docs_to_track = [
                {
                    "source": f["source"],
                    "title": f["title"],
                    "preview": f.get("preview"),
                    "metadata": {
                        "language": f.get("language"),
                        "file_path": f.get("file_path"),
                        "project": project,
                    },
                }
                for f in indexed_files
            ]
            await self._track_indexed_documents_batch(IndexType.CODE, docs_to_track)
            return count
        return await plugin.add_sources(sources)

    async def index_documentation(
        self,
        sources: list[str],
        project: str | None = None,
    ) -> int:
        """Index documentation files and track in database."""
        plugin = self._get_plugin(IndexType.DOCUMENTATION)
        if isinstance(plugin, DocsIndexPlugin):
            count, indexed_files = await plugin.index_sources(sources, project)

            # Batch track all indexed files using repository
            docs_to_track = [
                {
                    "source": f["source"],
                    "title": f["title"],
                    "preview": f.get("preview"),
                    "metadata": {
                        "doc_type": f.get("doc_type"),
                        "file_path": f.get("file_path"),
                        "project": project,
                    },
                }
                for f in indexed_files
            ]
            await self._track_indexed_documents_batch(
                IndexType.DOCUMENTATION, docs_to_track
            )
            return count
        return await plugin.add_sources(sources)

    async def _track_indexed_document(
        self,
        index_type: IndexType,
        source: str,
        title: str | None = None,
        preview: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        """Track an indexed document in the database for browsing/stats."""
        doc = {
            "source": source,
            "title": title,
            "preview": preview,
            "metadata": metadata,
        }
        await self._track_indexed_documents_batch(index_type, [doc])

    async def _track_indexed_documents_batch(
        self,
        index_type: IndexType,
        documents: list[dict],
    ) -> None:
        """Track multiple indexed documents in a single transaction."""
        from roboco.db import get_db_context
        from roboco.services.repositories import IndexedDocumentRepository

        if not documents:
            return

        async with get_db_context() as db:
            repo = IndexedDocumentRepository(db)
            await repo.upsert_batch(index_type.value, documents)

    async def index_conversation(self, params: IndexConversationParams) -> None:
        """Index a conversation message."""
        plugin = self._get_plugin(IndexType.CONVERSATIONS)
        if isinstance(plugin, ConversationsIndexPlugin):
            await plugin.index_message(params)
        else:
            await plugin.ingest(
                content=params.content,
                channel_id=params.channel_id,
                session_id=params.session_id,
                agent_id=params.agent_id,
                task_id=params.task_id,
                message_type=params.message_type,
            )

        # Track in database
        source = f"roboco://conversations/{params.session_id or 'unknown'}"
        await self._track_indexed_document(
            IndexType.CONVERSATIONS,
            source=source,
            title=f"Message in {params.channel_id or 'channel'}",
            preview=params.content[:500] if params.content else None,
            metadata={
                "channel_id": str(params.channel_id) if params.channel_id else None,
                "session_id": str(params.session_id) if params.session_id else None,
                "agent_id": str(params.agent_id) if params.agent_id else None,
            },
        )

    async def index_journal_entry(self, params: IndexJournalEntryParams) -> None:
        """Index a journal entry."""
        plugin = self._get_plugin(IndexType.JOURNALS)
        if isinstance(plugin, JournalsIndexPlugin):
            await plugin.index_entry(params)
        else:
            await plugin.ingest(
                content=params.content,
                entry_id=params.entry_id,
                agent_id=params.agent_id,
                entry_type=params.entry_type,
                task_id=params.task_id,
                tags=params.tags,
            )

        # Track in database
        source = f"roboco://journals/{params.entry_id or 'unknown'}"
        await self._track_indexed_document(
            IndexType.JOURNALS,
            source=source,
            title=f"Journal: {params.entry_type or 'entry'}",
            preview=params.content[:500] if params.content else None,
            metadata={
                "entry_id": str(params.entry_id) if params.entry_id else None,
                "agent_id": str(params.agent_id) if params.agent_id else None,
                "entry_type": params.entry_type,
                "tags": params.tags,
            },
        )

    # =========================================================================
    # INDEXING OPERATIONS (New - Optimal Brain)
    # =========================================================================

    async def index_error(self, params: IndexErrorParams) -> None:
        """Index an error pattern with solution."""
        plugin = self._get_plugin(IndexType.ERRORS)
        if isinstance(plugin, ErrorsIndexPlugin):
            await plugin.record_error(params)

        # Track in database
        import hashlib

        error_hash = hashlib.md5(params.error_message.encode()).hexdigest()[:12]
        source = f"roboco://errors/err-{error_hash}"
        await self._track_indexed_document(
            IndexType.ERRORS,
            source=source,
            title=f"Error: {params.error_message[:100]}",
            preview=f"{params.error_message}\n\nSolution: {params.solution}",
            metadata={
                "context": params.context,
                "worked": params.worked,
                "tags": params.tags,
            },
        )

    async def index_standard(self, params: IndexStandardParams) -> None:
        """Index a coding/security/workflow standard."""
        plugin = self._get_plugin(IndexType.STANDARDS)
        if isinstance(plugin, StandardsIndexPlugin):
            await plugin.index_standard(params)

        # Track in database
        source = f"roboco://standards/{params.domain or 'general'}"
        await self._track_indexed_document(
            IndexType.STANDARDS,
            source=source,
            title=f"Standard: {params.domain or 'General'}",
            preview=params.content[:500] if params.content else None,
            metadata={
                "domain": params.domain,
                "language": params.language,
                "scope": params.scope,
                "severity": params.severity,
            },
        )

    async def index_decision(self, params: IndexDecisionParams) -> None:
        """Index an architectural/design decision."""
        plugin = self._get_plugin(IndexType.DECISIONS)
        if isinstance(plugin, DecisionsIndexPlugin):
            await plugin.record_decision(params)

        # Track in database
        import hashlib

        topic_hash = hashlib.md5(params.topic.encode()).hexdigest()[:12]
        source = f"roboco://decisions/dec-{topic_hash}"
        await self._track_indexed_document(
            IndexType.DECISIONS,
            source=source,
            title=f"Decision: {params.topic[:100]}",
            preview=(
                f"{params.topic}\n\nDecision: {params.decision}\n\n"
                f"Rationale: {params.rationale}"
            ),
            metadata={
                "scope": params.scope,
                "tags": params.tags,
                "alternatives": params.alternatives,
            },
        )

    async def record_review(self, params: IndexReviewParams) -> str:
        """Record a code review for future reference."""
        plugin = self._get_plugin(IndexType.REVIEWS)
        doc_id = ""
        if isinstance(plugin, ReviewsIndexPlugin):
            review_params = ReviewParams(
                comment=params.summary,
                file_path=params.file_path,
                reviewer_id=params.reviewer_id,
                task_id=params.task_id,
                review_type="code",
                severity="info",
            )
            result = await plugin.record_review(review_params)
            doc_id = result.doc_id

        # Track in database
        source = f"roboco://reviews/{params.file_path or 'unknown'}"
        await self._track_indexed_document(
            IndexType.REVIEWS,
            source=source,
            title=f"Review: {params.file_path or 'Code'}",
            preview=params.summary[:500] if params.summary else None,
            metadata={
                "file_path": params.file_path,
                "reviewer_id": str(params.reviewer_id) if params.reviewer_id else None,
                "task_id": str(params.task_id) if params.task_id else None,
            },
        )

        return doc_id

    async def record_learning(self, params: LearningParams) -> str:
        """Record a learning for cross-agent knowledge sharing."""
        plugin = self._get_plugin(IndexType.LEARNINGS)
        doc_id = ""
        if isinstance(plugin, LearningsIndexPlugin):
            result = await plugin.record_learning(params)
            doc_id = result.doc_id

        # Track in database
        import hashlib

        content_hash = hashlib.md5(params.content.encode()).hexdigest()[:12]
        source = f"roboco://learnings/learn-{content_hash}"
        await self._track_indexed_document(
            IndexType.LEARNINGS,
            source=source,
            title=f"Learning: {params.category or 'General'}",
            preview=params.content[:500] if params.content else None,
            metadata={
                "category": params.category,
                "team": params.team,
                "shareable": params.shareable,
                "tags": params.tags,
            },
        )

        return doc_id

    async def index_standards_file(self, file_path: str) -> int:
        """Index a markdown standards file."""
        from pathlib import Path

        plugin = self._get_plugin(IndexType.STANDARDS)
        count = 0
        if isinstance(plugin, StandardsIndexPlugin):
            results = await plugin.index_markdown_file(file_path)
            count = len([r for r in results if r.success])

        # Track in database
        path = Path(file_path)
        if path.exists():
            await self._track_indexed_document(
                IndexType.STANDARDS,
                source=str(path.absolute()),
                title=path.stem.replace("-", " ").replace("_", " ").title(),
                preview=path.read_text(errors="ignore")[:500],
                metadata={"file_path": file_path},
            )

        return count

    # =========================================================================
    # SEARCH OPERATIONS
    # =========================================================================

    async def search(
        self,
        query: str,
        context: QueryContext | None = None,
        top_k: int = 5,
    ) -> list[SearchResult]:
        """
        Semantic search across knowledge base.

        Args:
            query: Natural language query
            context: Optional context for filtering results
            top_k: Number of results per index type

        Returns:
            List of search results sorted by relevance
        """
        if not self._initialized:
            raise RuntimeError("OptimalService not initialized")

        results: list[SearchResult] = []
        index_types = (
            context.index_types if context and context.index_types else list(IndexType)
        )

        for index_type in index_types:
            plugin = self._plugins.get(index_type)
            if plugin:
                try:
                    plugin_results = await plugin.search(query=query, top_k=top_k)
                    results.extend(plugin_results)
                except Exception as e:
                    logger.warning(
                        "Search failed for index",
                        index_type=index_type.value,
                        error=str(e),
                    )

        # Sort by score descending
        results.sort(key=lambda r: r.score, reverse=True)
        return results[: top_k * len(index_types)]

    async def query(
        self,
        query: str,
        context: QueryContext | None = None,
        top_k: int = 5,
    ) -> RAGResponse:
        """
        Query the knowledge base with RAG.

        Retrieves relevant context and generates an answer.
        """
        if not self._initialized:
            raise RuntimeError("OptimalService not initialized")

        index_types = (
            context.index_types if context and context.index_types else list(IndexType)
        )

        for index_type in index_types:
            plugin = self._plugins.get(index_type)
            if plugin:
                try:
                    answer, citations = await plugin.ask(query=query, top_k=top_k)
                    if answer:
                        return RAGResponse(
                            answer=answer,
                            citations=citations,
                            query=query,
                            context_used=len(citations),
                        )
                except Exception as e:
                    logger.warning(
                        "RAG query failed for index",
                        index_type=index_type.value,
                        error=str(e),
                    )

        return RAGResponse(
            answer="I couldn't find relevant information to answer your question.",
            citations=[],
            query=query,
            context_used=0,
        )

    # =========================================================================
    # SPECIALIZED SEARCH (Optimal Brain)
    # =========================================================================

    async def search_errors(
        self,
        error_message: str,
        context: str = "",
        top_k: int = 5,
    ) -> list[SearchResult]:
        """Search for known solutions to an error."""
        plugin = self._get_plugin(IndexType.ERRORS)
        if isinstance(plugin, ErrorsIndexPlugin):
            return await plugin.search_error(error_message, context, top_k)
        return await plugin.search(query=error_message, top_k=top_k)

    async def get_standards(
        self,
        domain: str,
        language: str | None = None,
        severity: str | None = None,
        top_k: int = 20,
    ) -> list[SearchResult]:
        """Get standards for a domain/language."""
        plugin = self._get_plugin(IndexType.STANDARDS)
        if isinstance(plugin, StandardsIndexPlugin):
            return await plugin.get_standards(domain, language, severity, top_k)
        return await plugin.search(query=f"{domain} standards", top_k=top_k)

    async def check_decision(
        self,
        topic: str,
        threshold: float = 0.7,
        top_k: int = 5,
    ) -> list[Any]:
        """Check for similar past decisions."""
        plugin = self._get_plugin(IndexType.DECISIONS)
        if isinstance(plugin, DecisionsIndexPlugin):
            return await plugin.check_decision(topic, threshold, top_k)
        results = await plugin.search(query=topic, top_k=top_k)
        return results

    async def search_learnings(
        self,
        query: str,
        category: str | None = None,
        team: str | None = None,
        top_k: int = 10,
    ) -> list[SearchResult]:
        """Search for relevant learnings."""
        plugin = self._get_plugin(IndexType.LEARNINGS)
        if isinstance(plugin, LearningsIndexPlugin):
            return await plugin.search_learnings(query, category, team, True, top_k)
        return await plugin.search(query=query, top_k=top_k)

    async def get_reviews_for_file(
        self,
        file_path: str,
        top_k: int = 10,
    ) -> list[SearchResult]:
        """Get past reviews for a file or similar files."""
        plugin = self._get_plugin(IndexType.REVIEWS)
        if isinstance(plugin, ReviewsIndexPlugin):
            return await plugin.get_reviews_for_file(file_path, top_k)
        return await plugin.search(query=f"review {file_path}", top_k=top_k)

    # =========================================================================
    # UTILITY OPERATIONS
    # =========================================================================

    async def get_stats(self) -> dict[str, Any]:
        """Get statistics about all indexes."""
        if not self._initialized:
            return {"initialized": False}

        stats: dict[str, Any] = {"initialized": True, "indexes": {}}
        for index_type, plugin in self._plugins.items():
            try:
                count = await plugin.count()
                stats["indexes"][index_type.value] = {"document_count": count}
            except Exception as e:
                stats["indexes"][index_type.value] = {"error": str(e)}

        return stats

    async def get_index_stats(self, index_type: IndexType) -> dict[str, Any]:
        """
        Get detailed stats for a specific index.

        Returns:
            Dict with document_count, chunk_count, last_updated
        """
        if not self._initialized:
            return {"error": "Not initialized"}

        from sqlalchemy import func, select

        from roboco.db import get_db_context
        from roboco.db.tables import IndexedDocumentTable

        plugin = self._get_plugin(index_type)
        chunk_count = await plugin.count()

        # Query DB for document count and last_updated
        async with get_db_context() as session:
            # Document count
            count_query = (
                select(func.count())
                .select_from(IndexedDocumentTable)
                .where(IndexedDocumentTable.index_type == index_type.value)
            )
            count_result = await session.execute(count_query)
            doc_count = count_result.scalar() or 0

            # Last updated
            last_updated_query = (
                select(func.max(IndexedDocumentTable.indexed_at))
                .select_from(IndexedDocumentTable)
                .where(IndexedDocumentTable.index_type == index_type.value)
            )
            last_updated_result = await session.execute(last_updated_query)
            last_updated = last_updated_result.scalar()

        return {
            "index_type": index_type.value,
            "document_count": doc_count,
            "chunk_count": chunk_count,
            "last_updated": last_updated.isoformat() if last_updated else None,
        }

    async def get_all_index_stats(self) -> dict[str, Any]:
        """
        Get detailed stats for all indexes including document counts and last_updated.

        Returns:
            Dict with initialized flag and indexes with full stats
        """
        if not self._initialized:
            return {"initialized": False, "indexes": {}}

        from sqlalchemy import func, select

        from roboco.db import get_db_context
        from roboco.db.tables import IndexedDocumentTable

        async with get_db_context() as session:
            stats: dict[str, Any] = {"initialized": True, "indexes": {}}

            for index_type, plugin in self._plugins.items():
                try:
                    chunk_count = await plugin.count()

                    # Document count
                    count_query = (
                        select(func.count())
                        .select_from(IndexedDocumentTable)
                        .where(IndexedDocumentTable.index_type == index_type.value)
                    )
                    count_result = await session.execute(count_query)
                    doc_count = count_result.scalar() or 0

                    # Last updated
                    last_updated_query = (
                        select(func.max(IndexedDocumentTable.indexed_at))
                        .select_from(IndexedDocumentTable)
                        .where(IndexedDocumentTable.index_type == index_type.value)
                    )
                    last_updated_result = await session.execute(last_updated_query)
                    last_updated = last_updated_result.scalar()

                    stats["indexes"][index_type.value] = {
                        "document_count": doc_count,
                        "chunk_count": chunk_count,
                        "last_updated": (
                            last_updated.isoformat() if last_updated else None
                        ),
                    }
                except Exception as e:
                    stats["indexes"][index_type.value] = {"error": str(e)}

            return stats

    async def auto_index_on_startup(
        self,
        code_sources: list[str] | None = None,
        docs_sources: list[str] | None = None,
        force: bool = False,
    ) -> dict[str, int]:
        """
        Auto-index code and docs if indexes are empty.

        Called during bootstrap to ensure RAG has content to search.

        Args:
            code_sources: Paths to index for code (default: auto-detect)
            docs_sources: Paths to index for docs (default: auto-detect)
            force: Force re-index even if not empty

        Returns:
            Dict with counts: {"code": N, "docs": M}
        """
        if not self._initialized:
            await self.initialize()

        # Auto-detect paths if not provided
        if code_sources is None:
            # Try Docker paths first, then local
            # ONLY use package directories (with __init__.py), NOT repo roots
            for path in ["/app/roboco", "roboco/"]:
                p = Path(path)
                if p.exists() and (p / "__init__.py").exists():
                    code_sources = [path]
                    break
            code_sources = code_sources or ["roboco/"]

        if docs_sources is None:
            # Try Docker paths first, then local
            for path in ["/app/docs", "docs/"]:
                if Path(path).exists():
                    docs_sources = [path]
                    break
            docs_sources = docs_sources or ["docs/"]

        result = {"code": 0, "docs": 0}

        # Check code index
        code_plugin = self._get_plugin(IndexType.CODE)
        code_count = await code_plugin.count()

        if code_count == 0 or force:
            logger.info(
                "Auto-indexing code",
                sources=code_sources,
                reason="empty" if code_count == 0 else "forced",
            )
            result["code"] = await self.index_code(code_sources, project="roboco")

        # Check docs index
        docs_plugin = self._get_plugin(IndexType.DOCUMENTATION)
        docs_count = await docs_plugin.count()

        if docs_count == 0 or force:
            logger.info(
                "Auto-indexing documentation",
                sources=docs_sources,
                reason="empty" if docs_count == 0 else "forced",
            )
            result["docs"] = await self.index_documentation(
                docs_sources, project="roboco"
            )

        if result["code"] > 0 or result["docs"] > 0:
            logger.info(
                "Auto-indexing complete",
                code_files=result["code"],
                doc_files=result["docs"],
            )
        else:
            logger.info("Indexes already populated, skipping auto-index")

        return result

    # =========================================================================
    # PROMPT TEMPLATE MANAGEMENT
    # =========================================================================

    def create_prompt_template(self, template_data: dict[str, Any]) -> dict[str, Any]:
        """
        Create a reusable prompt template.

        Args:
            template_data: Dict with id, name, template, description,
                          variables, category, created_at, created_by

        Raises:
            ValueError: If template_data is missing required 'id' field
        """
        if "id" not in template_data:
            raise ValueError("Template data must include 'id' field")
        template_id = template_data["id"]
        self._prompt_templates[template_id] = template_data
        return self._prompt_templates[template_id]

    def list_prompt_templates(
        self, category: str | None = None
    ) -> list[dict[str, Any]]:
        """List all prompt templates, optionally filtered by category."""
        templates = list(self._prompt_templates.values())
        if category:
            templates = [t for t in templates if t.get("category") == category]
        return templates

    def get_prompt_template(self, template_id: str) -> dict[str, Any] | None:
        """Get a prompt template by ID."""
        return self._prompt_templates.get(template_id)

    def delete_prompt_template(self, template_id: str) -> bool:
        """Delete a prompt template. Returns True if deleted."""
        if template_id in self._prompt_templates:
            del self._prompt_templates[template_id]
            return True
        return False

    def reset_prompt_templates(self) -> None:
        """Reset all prompt templates (for testing)."""
        self._prompt_templates.clear()

    async def clear_index(self, index_type: IndexType) -> None:
        """Clear a specific index."""
        plugin = self._get_plugin(index_type)
        await plugin.clear()
        logger.info("Cleared index", index_type=index_type.value)

    async def list_documents(
        self, index_type: IndexType, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        """
        List documents in a specific index.

        Returns list of documents with id, source, indexed_at, and metadata.
        """
        plugin = self._get_plugin(index_type)

        # Check if plugin has list_documents method
        if hasattr(plugin, "list_documents"):
            return await plugin.list_documents(limit=limit, offset=offset)

        # Fallback: return empty list if plugin doesn't support listing
        logger.warning(
            "Plugin does not support list_documents",
            index_type=index_type.value,
        )
        return []

    async def refresh_index(self, index_type: IndexType, sources: list[str]) -> None:
        """Refresh an index with new sources."""
        plugin = self._get_plugin(index_type)
        # For file-based indexes, re-add sources
        await plugin.add_sources(sources)
        logger.info("Refreshed index", index_type=index_type.value, sources=sources)

    # =========================================================================
    # BACKWARDS COMPATIBILITY - Document Ingestion
    # =========================================================================

    async def ingest_document(
        self,
        index_type: IndexType,
        content: str,
        metadata: dict[str, Any],
        doc_id: str | None = None,
    ) -> None:
        """
        Ingest a document with metadata directly into an index.

        This method maintains backwards compatibility with the old API.
        """
        plugin = self._get_plugin(index_type)
        await plugin.ingest(content=content, doc_id=doc_id, **metadata)


class _OptimalServiceHolder:
    """Holder for singleton OptimalService instance."""

    instance: OptimalService | None = None


async def get_optimal_service() -> OptimalService:
    """Get or create the OptimalService instance."""
    if _OptimalServiceHolder.instance is None:
        _OptimalServiceHolder.instance = OptimalService()
        await _OptimalServiceHolder.instance.initialize()
    return _OptimalServiceHolder.instance


async def close_optimal_service() -> None:
    """Close the OptimalService instance."""
    if _OptimalServiceHolder.instance is not None:
        await _OptimalServiceHolder.instance.close()
        _OptimalServiceHolder.instance = None
