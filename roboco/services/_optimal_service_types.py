"""Structural type for the composed OptimalService, TYPE_CHECKING-only.

``OptimalService`` is composed from mixins (``roboco/services/optimal.py``) whose
methods were moved verbatim out of the one class. mypy checks each mixin
against its own bases only, so a call into a sibling mixin, or into
OptimalService's own members, would not resolve.

Each mixin gives its class a TYPE_CHECKING-only base of ``OptimalServiceSelf``,
which declares that cross-class surface: ``FooSurface`` holds what ``Foo``
offers the other classes (``OptimalSurface`` what ``OptimalService`` itself does),
with each method's own decorators, parameters and return type, so no moved
body changes. ``tests/unit/services/test_optimal_service_mixins.py`` fails on a
stub its surface's class does not define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from roboco.models.optimal import IndexType
    from roboco.services.optimal_brain.indexes import BaseIndexPlugin


class OptimalDocsIndexingSurface(Protocol):
    """``OptimalDocsIndexing``'s members that other classes reach."""

    async def _track_indexed_document(
        self,
        index_type: IndexType,
        source: str,
        title: str | None = ...,
        preview: str | None = ...,
        metadata: dict | None = ...,
    ) -> None: ...
    async def index_documentation(
        self,
        sources: list[str],
        project: str | None = ...,
        provenance: str = ...,
        task_id: str | None = ...,
    ) -> int: ...


class OptimalIndexStatsSurface(Protocol):
    """``OptimalIndexStats``'s members that other classes reach."""

    async def get_stats(self) -> dict[str, Any]: ...


class OptimalKnowledgeIndexingSurface(Protocol):
    """``OptimalKnowledgeIndexing``'s members that other classes reach."""

    async def index_standards_file(self, file_path: str) -> int: ...


class OptimalPeriodicUpdateSurface(Protocol):
    """``OptimalPeriodicUpdate``'s members that other classes reach."""

    async def _reconcile_deleted_docs_from_db(self) -> None: ...
    async def _start_periodic_update(self) -> None: ...


class OptimalSurface(Protocol):
    """``OptimalService``'s members that other classes reach."""

    def _get_plugin(self, index_type: IndexType) -> BaseIndexPlugin: ...


class OptimalStartupSurface(Protocol):
    """``OptimalStartup``'s members that other classes reach."""

    async def _index_doc_file(self, md_file: Path, name: str) -> None: ...


class OptimalServiceSelf(
    OptimalDocsIndexingSurface,
    OptimalIndexStatsSurface,
    OptimalKnowledgeIndexingSurface,
    OptimalPeriodicUpdateSurface,
    OptimalSurface,
    OptimalStartupSurface,
    Protocol,
):
    """Every cross-class member a moved method body reaches via ``self``:
    OptimalService's instance attributes here, each class's
    members on its surface."""

    _docs_root: Path | None = None
    _file_mtimes: dict[str, float]
    _indexing_task: Any = None
    _initialized: bool = False
    _periodic_update_task: Any = None
    _plugins: dict[IndexType, BaseIndexPlugin]
    _prompt_templates: dict[str, dict[str, Any]]
