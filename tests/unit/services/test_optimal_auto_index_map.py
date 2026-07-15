"""docs/map is auto-indexed alongside docs/rag.

docs/map is the agent-facing exhaustive codebase map (CLAUDE.md); folding it
into OptimalService._auto_index_docs's auto_index_dirs makes it
roboco_kb_search-able. _index_docs_directory is generic (rglob *.md, route
only the ``standards`` subdir to the standards indexer), so no map-specific
branch is needed — every docs/map/*.md rides index_documentation like docs/rag.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from roboco.services.optimal import IndexingReport, OptimalService


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
