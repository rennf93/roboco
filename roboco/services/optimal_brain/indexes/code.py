"""
Code Index Plugin

Handles indexing and searching code files and repositories.
Uses a simple line-based chunking strategy instead of sentence-based,
since code doesn't have natural sentence boundaries.

Features:
- Incremental indexing: only re-embeds files that changed (via content hashing)
- Priority-based ordering: indexes models, services, api first
- Line-based chunking: respects code structure
"""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
from piragi.types import Chunk

from roboco.models.optimal import IndexType
from roboco.services.optimal_brain.indexes.base import BaseIndexPlugin, IngestResult

logger = structlog.get_logger()


@dataclass(frozen=True)
class _ChunkConfig:
    """Parameters controlling a chunking pass over code."""

    source: str
    chunk_overlap: int
    min_chunk: int


class FileHashRegistry:
    """
    Tracks file content hashes for incremental indexing.

    Persists hashes to disk so only changed files are re-embedded.
    """

    def __init__(self, cache_file: Path | None = None):
        """Initialize with optional cache file path."""
        self._cache_file = cache_file or Path(".piragi/file_hashes.json")
        self._hashes: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        """Load cached hashes from disk."""
        if self._cache_file.exists():
            try:
                self._hashes = json.loads(self._cache_file.read_text())
                logger.debug("Loaded file hash cache", count=len(self._hashes))
            except Exception as e:
                logger.warning("Failed to load file hash cache", error=str(e))
                self._hashes = {}

    def _save(self) -> None:
        """Persist hashes to disk."""
        try:
            self._cache_file.parent.mkdir(parents=True, exist_ok=True)
            self._cache_file.write_text(json.dumps(self._hashes, indent=2))
        except Exception as e:
            logger.warning("Failed to save file hash cache", error=str(e))

    @staticmethod
    def _hash_content(content: str) -> str:
        """Generate hash for file content."""
        return hashlib.md5(content.encode(), usedforsecurity=False).hexdigest()

    def has_changed(self, file_path: str, content: str) -> bool:
        """Check if file content has changed since last index."""
        current_hash = self._hash_content(content)
        stored_hash = self._hashes.get(file_path)
        return stored_hash != current_hash

    def update(self, file_path: str, content: str) -> None:
        """Update stored hash for a file."""
        self._hashes[file_path] = self._hash_content(content)

    def update_batch(self, files: list[tuple[str, str]]) -> None:
        """Update hashes for multiple files and save."""
        for file_path, content in files:
            self._hashes[file_path] = self._hash_content(content)
        self._save()

    def remove(self, file_path: str) -> None:
        """Remove a file from the registry."""
        self._hashes.pop(file_path, None)

    @property
    def count(self) -> int:
        """Number of tracked files."""
        return len(self._hashes)


# Patterns for detecting natural code boundaries
CODE_BOUNDARY_PATTERNS = [
    re.compile(r"^\s*(def|async def|class)\s+\w+"),  # Python
    re.compile(r"^\s*(function|const|let|var)\s+\w+"),  # JS/TS
    re.compile(r"^\s*(func|type|struct)\s+\w+"),  # Go
    re.compile(r"^\s*(fn|impl|struct|enum)\s+"),  # Rust
    re.compile(r"^\s*(public|private|protected|static)?\s*(void|int)"),  # Java
]


def _is_boundary_line(line: str) -> bool:
    """Check if line starts a logical code block (function, class, etc)."""
    stripped = line.lstrip()
    if not stripped:
        return False
    return any(pattern.match(stripped) for pattern in CODE_BOUNDARY_PATTERNS)


def _score_break_point(line: str) -> int:
    """Score how good a line is as a chunk break point (higher = better)."""
    stripped = line.strip()
    if not stripped:
        return 3  # Empty line - great break point
    if stripped in ("}", "end", "]"):
        return 2  # Closing brace/keyword
    if stripped.endswith(";") or stripped.endswith(":"):
        return 1  # Statement end
    return 0


def _find_best_break(lines: list[str], next_line: str) -> int:
    """Find the best break point index in a list of lines."""
    # If next line is a boundary (function/class), break at end
    if _is_boundary_line(next_line):
        return len(lines)

    best_break = len(lines)
    best_score = -1

    # Check last ~5 lines for good break points
    start = max(0, len(lines) - 5)
    for j in range(start, len(lines)):
        score = _score_break_point(lines[j])
        if score > best_score:
            best_score = score
            best_break = j + 1

    return best_break


def _calc_overlap(lines: list[str], max_overlap: int) -> list[str]:
    """Calculate overlap lines from end of a chunk."""
    overlap_lines: list[str] = []
    overlap_size = 0
    for line in reversed(lines):
        if overlap_size + len(line) + 1 > max_overlap:
            break
        overlap_lines.insert(0, line)
        overlap_size += len(line) + 1
    return overlap_lines


def _try_flush_chunk(
    chunks: list[Chunk],
    current_lines: list[str],
    line: str,
    config: _ChunkConfig,
) -> tuple[list[str], int]:
    """Attempt to flush a completed chunk, returning (new_current_lines, new_size).

    When the best candidate break is too small, keep accumulating (return
    the buffer unchanged).
    """
    best_break = _find_best_break(current_lines, line)
    break_lines = current_lines[:best_break]
    break_size = sum(len(ln) + 1 for ln in break_lines)

    if break_size < config.min_chunk:
        current_size = sum(len(ln) + 1 for ln in current_lines)
        return current_lines, current_size

    chunks.append(
        Chunk(
            text="\n".join(break_lines),
            source=config.source,
            chunk_index=len(chunks),
            metadata={},
        )
    )
    remaining = current_lines[best_break:]
    overlap = _calc_overlap(break_lines, config.chunk_overlap)
    new_current = overlap + remaining
    new_size = sum(len(ln) + 1 for ln in new_current)
    return new_current, new_size


def _finalize_trailing_chunk(
    chunks: list[Chunk],
    current_lines: list[str],
    config: _ChunkConfig,
) -> None:
    """Append or merge any remaining content into the chunk list."""
    if not current_lines:
        return
    chunk_text = "\n".join(current_lines)
    if len(chunk_text.strip()) >= config.min_chunk or not chunks:
        chunks.append(
            Chunk(
                text=chunk_text,
                source=config.source,
                chunk_index=len(chunks),
                metadata={},
            )
        )
    elif chunks:
        last = chunks[-1]
        chunks[-1] = Chunk(
            text=last.text + "\n" + chunk_text,
            source=config.source,
            chunk_index=last.chunk_index,
            metadata=last.metadata,
        )


def chunk_code(
    content: str,
    source: str,
    chunk_size: int = 1500,
    chunk_overlap: int = 150,
) -> list[Chunk]:
    """
    Chunk code using a smart boundary-aware strategy.

    Prefers breaking at function/class boundaries, falls back to blank lines.
    Uses reduced overlap since embedding cache avoids redundant work.

    Args:
        content: Source code content
        source: Source file path/URI
        chunk_size: Target chunk size in characters (~375 tokens)
        chunk_overlap: Overlap between chunks (reduced from 200)

    Returns:
        List of Chunk objects
    """
    if not content.strip():
        return []

    lines = content.split("\n")
    chunks: list[Chunk] = []
    current_lines: list[str] = []
    current_size = 0
    config = _ChunkConfig(source=source, chunk_overlap=chunk_overlap, min_chunk=300)

    for line in lines:
        line_size = len(line) + 1
        if current_size + line_size > chunk_size and current_lines:
            current_lines, current_size = _try_flush_chunk(
                chunks, current_lines, line, config
            )

        current_lines.append(line)
        current_size += line_size

    _finalize_trailing_chunk(chunks, current_lines, config)
    return chunks


# Common code file extensions
CODE_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".sh": "shell",
    ".sql": "sql",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".toml": "toml",
}

# Directories to skip during indexing (non-essential for understanding codebase)
SKIP_DIRECTORIES = {
    # VCS and build
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".piragi",
    "node_modules",
    ".next",
    "dist",
    "build",
    # Cache directories
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "htmlcov",
    ".tox",
    "eggs",
    "*.egg-info",
    # Tests and migrations (not critical for auto-indexing)
    "tests",
    "test",
    "alembic",
    "migrations",
    "seeds",
    "fixtures",
    "conftest",
}

# Priority directories to index first (most important for understanding codebase)
# Order matters: index these subdirectories first
PRIORITY_DIRECTORIES = [
    "agents",
    "api",
    "db",
    "enforcement",
    "events",
    "llm",
    "mcp",
    "models",
    "runtime",
    "services",
]

# Maximum files to auto-index on startup (prevent timeout)
MAX_AUTO_INDEX_FILES = 100


class CodeIndexPlugin(BaseIndexPlugin):
    """
    Plugin for indexing and searching code.

    Handles:
    - Source code files (Python, TypeScript, etc.)
    - Repository directories
    - Glob patterns for selective indexing

    Features:
    - Incremental indexing: skips unchanged files based on content hash
    - Priority ordering: indexes models, services, api first
    - Batch embedding: processes all files together for efficiency
    """

    def __init__(self) -> None:
        super().__init__()
        self._hash_registry: FileHashRegistry | None = None

    def _get_hash_registry(self) -> FileHashRegistry:
        """Lazy-load the hash registry."""
        if self._hash_registry is None:
            self._hash_registry = FileHashRegistry()
        return self._hash_registry

    @property
    def index_type(self) -> IndexType:
        return IndexType.CODE

    def prepare_metadata(
        self,
        content: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Prepare metadata for code content."""
        del content  # Unused - metadata comes from kwargs
        return {
            "type": "code",
            "project": kwargs.get("project", "default"),
            "language": kwargs.get("language"),
            "file_path": kwargs.get("file_path"),
        }

    def build_source_uri(self, doc_id: str | None = None, **kwargs: Any) -> str:
        """Build source URI for code."""
        file_path = kwargs.get("file_path", doc_id or "unknown")
        return f"roboco://code/{file_path}"

    def _is_valid_code_file(self, file_path: Path) -> bool:
        """Check if file is a valid code file for indexing."""
        if not file_path.is_file():
            return False
        if file_path.suffix not in CODE_EXTENSIONS:
            return False
        return not any(skip in file_path.parts for skip in SKIP_DIRECTORIES)

    def _collect_files_from_source(self, source: str) -> list[Path]:
        """Collect code files from a source path, directory, or glob."""
        source_path = Path(source)

        # Expand glob patterns and directories
        if "*" in source:
            candidates = list(Path().glob(source))
        elif source_path.is_dir():
            candidates = list(source_path.rglob("*"))
        elif source_path.exists():
            candidates = [source_path]
        else:
            logger.warning(f"Source not found: {source}")
            return []

        return [f for f in candidates if self._is_valid_code_file(f)]

    async def index_sources(
        self,
        sources: list[str],
        project: str | None = None,
        max_files: int | None = None,
        force_reindex: bool = False,
    ) -> tuple[int, list[dict[str, Any]]]:
        """
        Index code files/directories with incremental embedding.

        Uses content hashing to skip unchanged files, dramatically reducing
        re-indexing time. Only files with changed content are re-embedded.

        Files are sorted by priority: models, services, api, db, etc. come first.
        During auto-indexing, limits to MAX_AUTO_INDEX_FILES to prevent timeouts.

        Args:
            sources: List of file paths, directories, or glob patterns
            project: Optional project identifier for filtering
            max_files: Optional limit on files to index (for auto-indexing)
            force_reindex: If True, re-index all files regardless of hash

        Returns:
            Tuple of (count, indexed_files) where indexed_files contains
            metadata for each file indexed (for database tracking)
        """
        all_files = self._discover_and_prioritize_files(sources, max_files)
        if not all_files:
            return 0, []

        files_data, skipped_count = self._filter_changed_files(
            all_files, project, force_reindex
        )

        if skipped_count > 0:
            logger.info(
                f"Incremental indexing: {skipped_count} unchanged files skipped, "
                f"{len(files_data)} files to embed"
            )

        if not files_data:
            logger.info("No files need re-indexing (all unchanged)")
            return 0, []

        logger.info(f"Batch processing {len(files_data)} changed code files")

        results = await self._ingest_code_batch(files_data)
        count = sum(1 for r in results if r.success)

        if count > 0:
            hash_registry = self._get_hash_registry()
            indexed_hashes: list[tuple[str, str]] = [
                (str(data["file_path"].absolute()), data["content"])
                for data, result in zip(files_data, results, strict=True)
                if result.success
            ]
            hash_registry.update_batch(indexed_hashes)

        indexed_files = self._build_indexed_files(files_data)
        logger.info(
            f"Batch indexing complete: {count} files indexed, "
            f"{skipped_count} unchanged files skipped"
        )
        return count, indexed_files

    @staticmethod
    def _priority_key(path: Path) -> tuple[int, str]:
        """Sort key: priority dirs first, then by path string."""
        parts = path.parts
        for idx, priority_dir in enumerate(PRIORITY_DIRECTORIES):
            if priority_dir in parts:
                return (idx, str(path))
        return (len(PRIORITY_DIRECTORIES), str(path))

    def _discover_and_prioritize_files(
        self, sources: list[str], max_files: int | None
    ) -> list[Path]:
        """Collect candidate files from sources, sort by priority, apply limit."""
        all_files: list[Path] = []
        for source in sources:
            all_files.extend(self._collect_files_from_source(source))
        if not all_files:
            return all_files

        all_files.sort(key=self._priority_key)

        if max_files and len(all_files) > max_files:
            logger.info(
                f"Limiting to {max_files} files (found {len(all_files)})",
                priority_dirs=PRIORITY_DIRECTORIES[:4],
            )
            all_files = all_files[:max_files]

        logger.info(f"Found {len(all_files)} code files to check")
        return all_files

    def _filter_changed_files(
        self,
        all_files: list[Path],
        project: str | None,
        force_reindex: bool,
    ) -> tuple[list[dict[str, Any]], int]:
        """Read files and filter out unchanged ones. Returns (files_data, skipped)."""
        hash_registry = self._get_hash_registry()
        files_data: list[dict[str, Any]] = []
        skipped_count = 0

        for file_path in all_files:
            try:
                content = file_path.read_text(encoding="utf-8")
                file_key = str(file_path.absolute())

                file_changed = hash_registry.has_changed(file_key, content)
                if not force_reindex and not file_changed:
                    skipped_count += 1
                    continue

                language = CODE_EXTENSIONS.get(file_path.suffix)
                files_data.append(
                    {
                        "content": content,
                        "file_path": file_path,
                        "language": language,
                        "project": project or "default",
                    }
                )
            except Exception as e:
                logger.warning(
                    "Failed to read code file",
                    file=str(file_path),
                    error=str(e),
                )

        return files_data, skipped_count

    @staticmethod
    def _build_indexed_files(files_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Produce DB-tracking metadata entries for each file processed."""
        return [
            {
                "source": str(data["file_path"].absolute()),
                "title": data["file_path"].name,
                "preview": data["content"][:500] if data["content"] else None,
                "language": data["language"],
                "file_path": str(data["file_path"]),
            }
            for data in files_data
        ]

    async def _ingest_code_batch(
        self,
        files_data: list[dict[str, Any]],
    ) -> list[IngestResult]:
        """
        Batch ingest code files using line-based chunking.

        Unlike the base class ingest_batch which uses piragi's sentence-based
        chunker, this method uses a simple line-based chunker that's
        appropriate for source code.

        Args:
            files_data: List of dicts with content, file_path, language, project

        Returns:
            List of IngestResult for each file
        """
        import asyncio

        if not files_data:
            return []

        # Chunk ALL files using line-based chunker (fast, no tokenizer needed)
        all_chunks: list[Chunk] = []
        chunk_counts: dict[int, int] = {}

        for idx, data in enumerate(files_data):
            content = str(data["content"])
            file_path = str(data["file_path"])
            source = self.build_source_uri(file_path, file_path=file_path)

            # Use line-based chunking (1500 chars ≈ 375 tokens, with 200 char overlap)
            chunks = chunk_code(content, source, chunk_size=1500, chunk_overlap=200)

            # Add metadata to chunks
            metadata = self.prepare_metadata(
                content,
                file_path=file_path,
                language=data.get("language"),
                project=data.get("project", "default"),
            )
            for chunk in chunks:
                chunk.metadata = {**chunk.metadata, **metadata}

            all_chunks.extend(chunks)
            chunk_counts[idx] = len(chunks)

        logger.info(
            f"Code batch: {len(all_chunks)} chunks from {len(files_data)} files "
            f"(avg {len(all_chunks) / len(files_data):.1f} chunks/file)"
        )

        if not all_chunks:
            return [
                IngestResult(doc_id=str(d["file_path"]), chunk_count=0, success=True)
                for d in files_data
            ]

        # Embed and store using piragi's internals
        ragi_sync = self.ragi._sync

        def _embed_and_store() -> None:
            chunks_with_embeddings = ragi_sync.embedder.embed_chunks(all_chunks)
            ragi_sync.store.add_chunks(chunks_with_embeddings)

        try:
            await asyncio.to_thread(_embed_and_store)

            return [
                IngestResult(
                    doc_id=str(data["file_path"]),
                    chunk_count=chunk_counts.get(idx, 0),
                    success=True,
                )
                for idx, data in enumerate(files_data)
            ]
        except Exception as e:
            logger.error("Code batch ingest failed", error=str(e))
            return [
                IngestResult(
                    doc_id=str(data["file_path"]),
                    chunk_count=0,
                    success=False,
                    error=str(e),
                )
                for data in files_data
            ]
