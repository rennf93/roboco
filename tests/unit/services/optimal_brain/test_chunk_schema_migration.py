"""Guard the 030 chunk-schema migration against IndexType drift.

The migration that aligns the ``chunks_<index_type>`` tables with the in-house
vector store hardcodes the table list (so it stays self-contained and never
imports evolving app code). This test is the other half of that contract: it
fails the moment a new ``IndexType`` is added without extending the migration,
which is exactly the gap that let the piragi-era ``text`` columns survive the
engine swap and break every ingest/search.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from roboco.models.optimal import IndexType

if TYPE_CHECKING:
    from types import ModuleType


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "alembic" / "versions").is_dir():
            return parent
    raise RuntimeError("could not locate alembic/versions from the test file")


@pytest.fixture(scope="module")
def migration() -> ModuleType:
    path = _repo_root() / "alembic" / "versions" / "030_rag_chunks_content_schema.py"
    spec = importlib.util.spec_from_file_location("_migration_030", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_chunk_tables_match_index_type_enum(migration: ModuleType) -> None:
    expected = {f"chunks_{t.value}" for t in IndexType}
    assert set(migration.CHUNK_TABLES) == expected, (
        "CHUNK_TABLES is out of sync with IndexType — a new index type was added "
        "without extending the 030 chunk-schema migration."
    )


def test_chunk_tables_has_no_duplicates(migration: ModuleType) -> None:
    assert len(migration.CHUNK_TABLES) == len(set(migration.CHUNK_TABLES))


def test_revision_chain_is_wired(migration: ModuleType) -> None:
    assert migration.revision == "030_rag_chunks_content_schema"
    assert migration.down_revision == "029_project_quality_command"


def test_upgrade_and_downgrade_are_callable(migration: ModuleType) -> None:
    assert callable(migration.upgrade)
    assert callable(migration.downgrade)
