"""docs/map is auto-indexed alongside docs/rag.

docs/map is the agent-facing exhaustive codebase map (CLAUDE.md); folding it
into OptimalService._auto_index_docs's auto_index_dirs makes it
roboco_kb_search-able. _index_docs_directory is generic (rglob *.md, route
only the ``standards`` subdir to the standards indexer), so no map-specific
branch is needed — every docs/map/*.md rides index_documentation like docs/rag.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from roboco.services.optimal import IndexingReport, OptimalService

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_auto_index_docs_includes_map_subdir() -> None:
    """_auto_index_docs calls _index_docs_directory once per auto_index_dirs
    entry that exists under the resolved docs root — and docs/map exists in
    this repo, so the map call must fire. The subdir name is the 2nd positional
    arg (target_dir, name, _force=force)."""
    svc = object.__new__(OptimalService)
    mock = AsyncMock(return_value=IndexingReport(index_type="docs/x"))

    with patch.object(svc, "_index_docs_directory", new=mock):
        await svc._auto_index_docs()

    names = [c.args[1] for c in mock.call_args_list]
    assert "rag" in names
    assert "map" in names


@pytest.mark.asyncio
async def test_periodic_check_reindexes_modified_map_file(tmp_path: Path) -> None:
    """The periodic loop must watch docs/map too, not just docs/rag — a
    modified docs/map file with an already-tracked (stale) mtime is picked
    up by _check_for_updates and routed through _index_doc_file with the
    "map" dir name, exactly like the one-shot path resolves it."""
    docs_root = tmp_path / "docs"
    (docs_root / "rag").mkdir(parents=True)
    (docs_root / "map").mkdir(parents=True)
    map_file = docs_root / "map" / "CLAUDE.md"
    map_file.write_text("# initial")

    svc = object.__new__(OptimalService)
    svc._docs_root = docs_root
    # Simulate this file was already indexed at startup with a stale mtime,
    # so the next scan sees it as modified.
    svc._file_mtimes = {str(map_file): 0.0}

    mock = AsyncMock()
    with patch.object(svc, "_index_doc_file", new=mock):
        await svc._check_for_updates()

    mock.assert_awaited_once_with(map_file, "map")
