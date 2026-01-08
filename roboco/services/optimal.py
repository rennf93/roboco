"""
Optimal API Service (Refactored with Plugin Architecture)

Knowledge base, RAG queries, and prompt optimization using piragi.
This service provides semantic search across documentation,
conversations, journal entries, errors, standards, decisions, reviews, and learnings.

NOTE: Code indexing has been deprecated due to slow CPU embedding and poor quality.

The service uses a plugin-based architecture where each index type is handled
by a specialized plugin that implements the BaseIndexPlugin interface.
"""

import re
from dataclasses import dataclass, field
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

# Max chars per citation content - increased for better synthesis quality
# qwen3-embedding:0.6b retrieves higher quality chunks, so more context helps
MAX_CONTENT_CHARS = 800


@dataclass
class IndexingReport:
    """
    Detailed report of indexing operation results.

    Provides visibility into what was indexed successfully vs what failed,
    enabling proper error handling and recovery.
    """

    index_type: str
    total_attempted: int = 0
    successful: int = 0
    failed: int = 0
    skipped: int = 0  # Already indexed or filtered out
    failed_sources: list[tuple[str, str]] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def success_rate(self) -> float:
        """Percentage of attempted items that succeeded."""
        if self.total_attempted == 0:
            return 100.0
        return (self.successful / self.total_attempted) * 100

    @property
    def has_failures(self) -> bool:
        """True if any items failed."""
        return self.failed > 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "index_type": self.index_type,
            "total_attempted": self.total_attempted,
            "successful": self.successful,
            "failed": self.failed,
            "skipped": self.skipped,
            "success_rate": round(self.success_rate, 1),
            "has_failures": self.has_failures,
            "failed_sources": self.failed_sources[:10],  # Limit for API
            "duration_seconds": round(self.duration_seconds, 2),
        }


@dataclass
class AutoIndexReport:
    """Combined report for auto-indexing on startup."""

    code: IndexingReport | None = None
    documentation: IndexingReport | None = None
    overall_success: bool = True
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        docs = self.documentation.to_dict() if self.documentation else None
        return {
            "code": self.code.to_dict() if self.code else None,
            "documentation": docs,
            "overall_success": self.overall_success,
            "warnings": self.warnings,
        }


# Plugin registry mapping IndexType to plugin class
PLUGIN_REGISTRY: dict[IndexType, type[BaseIndexPlugin]] = {
    # IndexType.CODE removed - deprecated due to slow CPU embedding and poor retrieval
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
        self._indexing_task: Any = None  # Background indexing task
        self._periodic_update_task: Any = None  # Periodic update task
        self._file_mtimes: dict[str, float] = {}  # Track file modification times
        self._docs_root: Path | None = None  # Cached docs root path

    async def initialize(self) -> None:
        """
        Initialize all knowledge base indexes.

        Uses graceful degradation - if a plugin fails to initialize, log the error
        and continue with remaining plugins. The service will function with
        reduced capabilities rather than completely failing.
        """
        if self._initialized:
            return

        logger.info("Initializing OptimalService with plugin architecture")

        # Track initialization results for reporting
        initialized_count = 0
        failed_plugins: list[tuple[IndexType, str]] = []

        import asyncio

        # Per-plugin initialization timeout (embedding validation can be slow)
        plugin_init_timeout = 30.0

        # Create and initialize plugins for each index type
        for index_type, plugin_class in PLUGIN_REGISTRY.items():
            try:
                plugin = plugin_class()
                async with asyncio.timeout(plugin_init_timeout):
                    await plugin.initialize()
                self._plugins[index_type] = plugin
                initialized_count += 1
                logger.info(f"Initialized {index_type.value} plugin")
            except TimeoutError:
                error_msg = f"Plugin init timed out ({plugin_init_timeout}s)"
                failed_plugins.append((index_type, error_msg))
                logger.error(
                    "Plugin initialization timeout - continuing with degraded mode",
                    index_type=index_type.value,
                    timeout=plugin_init_timeout,
                )
            except Exception as e:
                # Log error but continue with other plugins
                error_msg = str(e)
                failed_plugins.append((index_type, error_msg))
                logger.error(
                    "Failed to initialize plugin - continuing with degraded mode",
                    index_type=index_type.value,
                    error=error_msg,
                )

        # Service is initialized if at least one plugin succeeded
        if initialized_count > 0:
            self._initialized = True
            logger.info(
                "OptimalService initialization complete",
                initialized=initialized_count,
                failed=len(failed_plugins),
            )
        else:
            # All plugins failed - this is a critical error
            raise RuntimeError(
                f"OptimalService failed to initialize any plugins. "
                f"Errors: {failed_plugins}"
            )

        # Report failed plugins for debugging
        if failed_plugins:
            logger.warning(
                "Some index plugins failed to initialize",
                failed=[f"{idx.value}: {err}" for idx, err in failed_plugins],
            )

        # Warm up embedding model to avoid cold start latency on first query
        await self._warmup_embedder()

        # Auto-index code and documentation on startup (truly non-blocking)
        # Run in background so API can start accepting requests immediately
        self._indexing_task = asyncio.create_task(self._auto_index_on_startup_safe())

    async def _warmup_embedder(self) -> None:
        """Warm up the embedding model to avoid cold start latency."""
        from roboco.services.optimal_brain.shared_embedder import (
            get_shared_embedder,
        )

        try:
            embedder = await get_shared_embedder()
            if hasattr(embedder, "aembed_query"):
                await embedder.aembed_query("warmup")
            logger.info("Embedding model warmed up successfully")
        except Exception as e:
            logger.warning("Embedding warm-up failed (non-fatal)", error=str(e))

    async def _auto_index_on_startup_safe(self) -> None:
        """
        Safe wrapper for auto-indexing that catches all errors.

        Runs auto-indexing in background without blocking API startup.
        Logs errors but doesn't crash the service if Ollama is unavailable.
        After startup indexing, starts periodic update task if enabled.
        """
        try:
            report = await self._auto_index_on_startup()
            if report.warnings:
                logger.warning(
                    "Auto-indexing completed with warnings",
                    warnings=report.warnings,
                )
            else:
                logger.info(
                    "Auto-indexing completed successfully",
                    code_indexed=report.code.successful if report.code else 0,
                    docs_indexed=report.documentation.successful
                    if report.documentation
                    else 0,
                )
        except Exception as e:
            logger.error(
                "Auto-indexing failed - service operational but indexes may be empty",
                error=str(e),
            )

        # Start periodic update task if enabled
        await self._start_periodic_update()

    async def _auto_index_on_startup(self, force: bool = False) -> AutoIndexReport:
        """
        Auto-index documentation on startup.

        Indexes:
        - /docs/standards/ - Coding, security, workflow standards
        - /docs/workflows/ - Agent workflow documentation

        NOTE: Code indexing has been deprecated.

        Args:
            force: If True, reindex even if indexes already have content

        Returns:
            AutoIndexReport with detailed results for each index type
        """
        report = AutoIndexReport()
        report.code = None  # Code indexing deprecated

        # Index documentation
        docs_report = await self._auto_index_docs(force=force)
        report.documentation = docs_report
        if docs_report and docs_report.has_failures:
            report.warnings.append(
                f"Documentation indexing had {docs_report.failed} failures"
            )

        # Overall success if at least something was indexed
        total_successful = docs_report.successful if docs_report else 0
        report.overall_success = total_successful > 0 or not report.warnings

        return report

    async def _auto_index_docs(self, force: bool = False) -> IndexingReport | None:
        """Auto-index documentation directories on startup."""
        import time

        report = IndexingReport(index_type="documentation")
        start_time = time.time()

        # Find the docs directory
        possible_docs_roots = [
            Path("/app/docs"),
            Path(__file__).parent.parent.parent / "docs",
            Path.cwd() / "docs",
        ]

        docs_root = None
        for path in possible_docs_roots:
            if path.exists() and path.is_dir():
                docs_root = path
                self._docs_root = path  # Cache for periodic updates
                break

        if docs_root is None:
            logger.warning(
                "Docs directory not found",
                searched_paths=[str(p) for p in possible_docs_roots],
            )
            return None

        # Directories to auto-index (RAG-optimized docs only)
        auto_index_dirs = ["rag"]

        for subdir in auto_index_dirs:
            target_dir = docs_root / subdir
            if not target_dir.exists():
                continue

            subdir_report = await self._index_docs_directory(
                target_dir, subdir, _force=force
            )
            # Aggregate results
            report.total_attempted += subdir_report.total_attempted
            report.successful += subdir_report.successful
            report.failed += subdir_report.failed
            report.skipped += subdir_report.skipped
            report.failed_sources.extend(subdir_report.failed_sources)

        report.duration_seconds = time.time() - start_time
        return report

    async def _index_docs_directory(
        self, directory: Path, name: str, _force: bool = False
    ) -> IndexingReport:
        """Index all markdown files in a documentation directory."""
        report = IndexingReport(index_type=f"docs/{name}")

        md_files = list(directory.rglob("*.md"))
        if not md_files:
            logger.info(f"No files found to index in {name}/", path=str(directory))
            return report

        report.total_attempted = len(md_files)
        logger.info(
            f"Auto-indexing {name} files",
            directory=str(directory),
            file_count=len(md_files),
        )

        # Index each file with individual error tracking
        for md_file in md_files:
            try:
                # Use standards indexer for files in standards subdirectory
                is_standards = name == "standards" or "standards" in md_file.parts
                if is_standards:
                    await self.index_standards_file(str(md_file))
                    report.successful += 1
                else:
                    await self.index_documentation([str(md_file)])
                    report.successful += 1

                # Track mtime for periodic update detection
                import contextlib

                with contextlib.suppress(OSError):
                    self._file_mtimes[str(md_file)] = md_file.stat().st_mtime

                logger.debug(f"Indexed {name} file", file=str(md_file))
            except Exception as e:
                error_msg = str(e)
                report.failed += 1
                report.failed_sources.append((str(md_file), error_msg))
                logger.warning(
                    f"Failed to index {name} file",
                    file=str(md_file),
                    error=error_msg,
                )

        logger.info(
            f"{name.capitalize()} auto-indexing complete",
            successful=report.successful,
            failed=report.failed,
            total=report.total_attempted,
        )
        return report

    # =========================================================================
    # PERIODIC UPDATE (File Change Detection)
    # =========================================================================

    async def _start_periodic_update(self) -> None:
        """Start periodic update task if enabled in config."""
        import asyncio

        from roboco.config import get_settings

        settings = get_settings()
        if not settings.rag_auto_update_enabled:
            logger.info("RAG auto-update disabled in config")
            return

        interval = settings.rag_auto_update_interval
        logger.info(
            "Starting RAG periodic update task",
            interval_seconds=interval,
        )
        self._periodic_update_task = asyncio.create_task(
            self._periodic_update_loop(interval)
        )

    async def _periodic_update_loop(self, interval: int) -> None:
        """Background loop that checks for file changes periodically."""
        import asyncio

        while True:
            try:
                await asyncio.sleep(interval)
                await self._check_for_updates()
            except asyncio.CancelledError:
                logger.info("Periodic update task cancelled")
                break
            except Exception as e:
                logger.error("Periodic update check failed", error=str(e))
                # Continue running despite errors

    def _resolve_docs_root(self) -> Path | None:
        """Resolve and cache the docs root directory."""
        if self._docs_root is not None:
            return self._docs_root

        possible_docs_roots = [
            Path("/app/docs"),
            Path(__file__).parent.parent.parent / "docs",
            Path.cwd() / "docs",
        ]
        for path in possible_docs_roots:
            if path.exists() and path.is_dir():
                self._docs_root = path
                return path
        return None

    async def _check_for_updates(self) -> None:
        """Scan for new or modified files and index them."""
        docs_root = self._resolve_docs_root()
        if docs_root is None:
            return

        rag_dir = docs_root / "rag"
        if not rag_dir.exists():
            return

        new_files: list[Path] = []
        modified_files: list[Path] = []

        for md_file in rag_dir.rglob("*.md"):
            file_path = str(md_file)
            try:
                current_mtime = md_file.stat().st_mtime
            except OSError:
                continue

            if file_path not in self._file_mtimes:
                new_files.append(md_file)
                self._file_mtimes[file_path] = current_mtime
            elif current_mtime > self._file_mtimes[file_path]:
                modified_files.append(md_file)
                self._file_mtimes[file_path] = current_mtime

        files_to_index = new_files + modified_files
        if not files_to_index:
            return

        logger.info(
            "Detected file changes, re-indexing",
            new_count=len(new_files),
            modified_count=len(modified_files),
        )

        indexed = 0
        for md_file in files_to_index:
            try:
                await self.index_documentation([str(md_file)])
                indexed += 1
                logger.debug("Re-indexed file", file=str(md_file))
            except Exception as e:
                logger.warning(
                    "Failed to re-index file",
                    file=str(md_file),
                    error=str(e),
                )

        if indexed > 0:
            logger.info(
                "Periodic update complete",
                indexed=indexed,
                total_files=len(files_to_index),
            )

    async def close(self) -> None:
        """Cleanup resources."""
        import asyncio
        import contextlib

        # Cancel periodic update task
        if self._periodic_update_task and not self._periodic_update_task.done():
            self._periodic_update_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._periodic_update_task

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
        max_files: int | None = None,
    ) -> int:
        """DEPRECATED: Code indexing has been removed.

        Code indexing was deprecated due to:
        - Slow CPU-based embedding (no GPU available)
        - Poor retrieval quality with current embedding model
        - Better results achieved by focusing on documentation/standards
        """
        _ = sources, project, max_files  # Unused
        logger.warning("index_code() is deprecated and does nothing")
        return 0

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
                outcome = await plugin.search(query=query, top_k=top_k)
                if outcome.success:
                    results.extend(outcome.results)
                else:
                    logger.warning(
                        "Search failed for index",
                        index_type=index_type.value,
                        error=outcome.error_message,
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

        Aggregates citations from all indexes first, then synthesizes a single
        answer from the best sources. This ensures quality by using the most
        relevant content regardless of which index it comes from.
        """
        if not self._initialized:
            raise RuntimeError("OptimalService not initialized")

        index_types = (
            context.index_types if context and context.index_types else list(IndexType)
        )

        logger.info(
            "RAG query starting", query=query[:50], num_indexes=len(index_types)
        )

        # Aggregate citations from all non-empty indexes (search only, no LLM)
        all_citations: list[SearchResult] = []
        search_stats: dict[str, int] = {}
        search_errors: dict[str, str] = {}

        for index_type in index_types:
            plugin = self._plugins.get(index_type)
            if not plugin:
                continue

            # Skip empty indexes
            count = await plugin.count()
            if count == 0:
                logger.debug(
                    "Skipping empty index",
                    index_type=index_type.value,
                )
                continue

            # Use search() directly instead of ask() to avoid multiple LLM calls
            outcome = await plugin.search(query=query, top_k=top_k)
            if outcome.success:
                search_stats[index_type.value] = len(outcome.results)
                all_citations.extend(outcome.results)
            else:
                search_stats[index_type.value] = -1  # -1 indicates error
                search_errors[index_type.value] = outcome.error_message or "Unknown"
                logger.warning(
                    "RAG search failed for index",
                    index_type=index_type.value,
                    error=outcome.error_message,
                )

        logger.info(
            "RAG search complete",
            total_citations=len(all_citations),
            by_index=search_stats,
            errors=search_errors if search_errors else None,
        )

        # Sort all citations by score and take top results
        all_citations.sort(key=lambda r: r.score, reverse=True)
        top_citations = all_citations[: top_k * 2]

        # Synthesize a single answer from the best aggregated citations
        if top_citations:
            logger.info(
                "Synthesizing answer from aggregated citations",
                num_citations=len(top_citations),
            )
            answer = await self._synthesize_from_citations(query, top_citations)
            if answer:
                return RAGResponse(
                    answer=answer,
                    citations=top_citations,
                    query=query,
                    context_used=len(top_citations),
                    search_stats=search_stats,
                    search_errors=search_errors,
                )

        logger.warning("RAG query found no citations in any index")
        return RAGResponse(
            answer="I couldn't find relevant information to answer your question.",
            citations=[],
            query=query,
            context_used=0,
            search_stats=search_stats,
            search_errors=search_errors,
        )

    def _strip_think_tags(self, text: str) -> str:
        """Strip <think> tags from LLM response, extracting content if needed."""
        # First try: get content outside think tags
        outside = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
        outside = re.sub(r"</think>", "", outside).strip()
        if outside:
            return outside

        # If empty, extract content FROM inside think tags
        inside_match = re.search(r"<think>(.*?)</think>", text, flags=re.DOTALL)
        if inside_match:
            return inside_match.group(1).strip()

        return text.strip()

    def _build_fallback_summary(self, citations: list[SearchResult]) -> str:
        """Build fallback summary from citations when LLM unavailable."""
        if not citations:
            return "No relevant information found in the knowledge base."

        parts = ["Here's what I found in the knowledge base:\n"]
        for c in citations[:5]:
            source_type = c.index_type.value if c.index_type else "unknown"
            parts.append(f"**[{source_type}] {c.source}**")
            parts.append(c.content[:600])
            parts.append("")

        parts.append(
            "\n*Note: This is a direct extract from the knowledge base. "
            "Review the sources above for detailed guidance.*"
        )
        return "\n".join(parts)

    async def _synthesize_from_citations(
        self,
        query: str,
        citations: list[SearchResult],
    ) -> str:
        """
        Synthesize an answer from aggregated citations using the local LLM.

        Called when individual indexes returned citations but no LLM answer
        (e.g., due to timeouts or errors). Includes retry logic for transient
        failures.
        """
        import asyncio

        import httpx

        from roboco.config import settings

        if not citations:
            return ""

        # Build context from citations
        context_parts: list[str] = []
        for citation in citations[:8]:
            idx_type = citation.index_type
            source_type = idx_type.value if idx_type else "unknown"
            content = (
                citation.content[:MAX_CONTENT_CHARS] + "..."
                if len(citation.content) > MAX_CONTENT_CHARS
                else citation.content
            )
            context_parts.append(f"[{source_type}] {content}")

        context = "\n\n---\n\n".join(context_parts)

        prompt = (
            "You are a senior technical advisor helping AI agents. "
            "Based on the knowledge base context below, provide a thorough answer.\n\n"
            "Your response MUST include:\n"
            "- Clear explanation of the concept or solution\n"
            "- Specific steps or code examples when relevant\n"
            "- References to standards, decisions, or learnings from context\n"
            "- Warnings about common pitfalls if applicable\n\n"
            "Do NOT give vague or generic advice. Be specific and actionable.\n"
            "Do NOT use <think> tags.\n\n"
            f"Context:\n{context}\n\n"
            f"Question: {query}\n\n"
            "Provide a detailed, helpful response:"
        )

        max_retries = 3
        retry_delay_base = 0.5

        async with httpx.AsyncClient(timeout=30.0) as client:
            for attempt in range(max_retries):
                try:
                    resp = await client.post(
                        f"{settings.local_llm_base_url}/chat/completions",
                        json={
                            "model": settings.local_llm_model,
                            "messages": [{"role": "user", "content": prompt}],
                            "max_tokens": 4096,
                            "options": {"num_ctx": 8192},
                        },
                    )
                    if resp.is_success:
                        data = resp.json()
                        answer = self._strip_think_tags(
                            data["choices"][0]["message"]["content"]
                        )
                        if answer:
                            return answer
                        logger.warning("LLM response was all thinking tags")
                        break

                    if resp.status_code >= httpx.codes.INTERNAL_SERVER_ERROR:
                        logger.warning(
                            "Synthesis LLM server error",
                            status=resp.status_code,
                            attempt=attempt + 1,
                        )
                    else:
                        logger.warning("Synthesis LLM failed", status=resp.status_code)
                        break

                except (httpx.TimeoutException, httpx.ConnectError) as e:
                    logger.warning("Synthesis LLM error", attempt=attempt + 1, error=e)
                except Exception as e:
                    logger.warning("Synthesis failed", error=str(e))
                    break

                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay_base * (2**attempt))

        return self._build_fallback_summary(citations)

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
        outcome = await plugin.search(query=error_message, top_k=top_k)
        return outcome.results

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
        outcome = await plugin.search(query=f"{domain} standards", top_k=top_k)
        return outcome.results

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
        outcome = await plugin.search(query=topic, top_k=top_k)
        return outcome.results

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
        outcome = await plugin.search(query=query, top_k=top_k)
        return outcome.results

    async def get_reviews_for_file(
        self,
        file_path: str,
        top_k: int = 10,
    ) -> list[SearchResult]:
        """Get past reviews for a file or similar files."""
        plugin = self._get_plugin(IndexType.REVIEWS)
        if isinstance(plugin, ReviewsIndexPlugin):
            return await plugin.get_reviews_for_file(file_path, top_k)
        outcome = await plugin.search(query=f"review {file_path}", top_k=top_k)
        return outcome.results

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

    async def check_index_staleness(
        self,
        index_type: IndexType | None = None,
    ) -> dict[str, Any]:
        """
        Check if indexes are stale (source files modified after last indexing).

        This helps detect when a reindex is needed because files have changed.

        Args:
            index_type: Specific index to check, or None for CODE and DOCUMENTATION

        Returns:
            Dict with staleness info per index type
        """
        from datetime import UTC, datetime

        from sqlalchemy import func, select

        from roboco.db import get_db_context
        from roboco.db.tables import IndexedDocumentTable

        result: dict[str, Any] = {"stale_indexes": [], "details": {}}

        # Only check DOCUMENTATION (file-based index) - CODE deprecated
        indexes_to_check = [index_type] if index_type else [IndexType.DOCUMENTATION]

        async with get_db_context() as session:
            for idx_type in indexes_to_check:
                if idx_type not in self._plugins:
                    continue

                # Get last indexed time
                last_indexed_query = (
                    select(func.max(IndexedDocumentTable.indexed_at))
                    .select_from(IndexedDocumentTable)
                    .where(IndexedDocumentTable.index_type == idx_type.value)
                )
                last_indexed_result = await session.execute(last_indexed_query)
                last_indexed = last_indexed_result.scalar()

                if last_indexed is None:
                    # Never indexed
                    result["stale_indexes"].append(idx_type.value)
                    result["details"][idx_type.value] = {
                        "status": "never_indexed",
                        "last_indexed": None,
                        "recommendation": "Run /kb/reindex to index this content",
                    }
                    continue

                # Get indexed source paths
                sources_query = (
                    select(IndexedDocumentTable.source)
                    .where(IndexedDocumentTable.index_type == idx_type.value)
                    .distinct()
                )
                sources_result = await session.execute(sources_query)
                indexed_sources = [row[0] for row in sources_result.fetchall()]

                # Check if any source files are newer than last_indexed
                stale_files: list[str] = []
                for source in indexed_sources[:100]:  # Limit check to 100 files
                    source_path = Path(source)
                    if source_path.exists():
                        try:
                            mtime = datetime.fromtimestamp(
                                source_path.stat().st_mtime, tz=UTC
                            )
                            if mtime > last_indexed:
                                stale_files.append(source)
                        except OSError:
                            pass  # Skip files we can't stat

                if stale_files:
                    result["stale_indexes"].append(idx_type.value)
                    result["details"][idx_type.value] = {
                        "status": "stale",
                        "last_indexed": last_indexed.isoformat(),
                        "stale_file_count": len(stale_files),
                        "stale_files_sample": stale_files[:5],
                        "recommendation": "Run /kb/reindex?force=true to update",
                    }
                else:
                    result["details"][idx_type.value] = {
                        "status": "current",
                        "last_indexed": last_indexed.isoformat(),
                        "indexed_sources_count": len(indexed_sources),
                    }

        result["needs_reindex"] = len(result["stale_indexes"]) > 0
        return result

    async def auto_index_on_startup(
        self,
        code_sources: list[str] | None = None,
        docs_sources: list[str] | None = None,
        force: bool = False,
    ) -> dict[str, int]:
        """
        Auto-index docs if indexes are empty.

        Called during bootstrap to ensure RAG has content to search.

        NOTE: Code indexing has been deprecated.

        Args:
            code_sources: DEPRECATED - ignored
            docs_sources: Paths to index for docs (default: auto-detect)
            force: Force re-index even if not empty

        Returns:
            Dict with counts: {"code": 0, "docs": M}
        """
        _ = code_sources  # Deprecated
        if not self._initialized:
            await self.initialize()

        if docs_sources is None:
            # Try Docker paths first, then local
            for path in ["/app/docs", "docs/"]:
                if Path(path).exists():
                    docs_sources = [path]
                    break
            docs_sources = docs_sources or ["docs/"]

        result = {"code": 0, "docs": 0}  # code always 0 - deprecated

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

        if result["docs"] > 0:
            logger.info("Auto-indexing complete", doc_files=result["docs"])
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
