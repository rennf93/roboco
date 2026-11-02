"""Structural type for the composed BaseIndexPlugin, TYPE_CHECKING-only.

``BaseIndexPlugin`` is composed from mixins
(``roboco/services/optimal_brain/indexes/base.py``) whose methods were moved verbatim
out of the one class. mypy checks each mixin against its own bases only, so a call into
a sibling mixin, or into BaseIndexPlugin's own members, would not resolve.

Each mixin gives its class a TYPE_CHECKING-only base of ``BaseIndexPluginSelf``, which
declares that cross-class surface: ``FooSurface`` holds what ``Foo`` offers the other
classes (``BaseIndexPluginSurface`` what ``BaseIndexPlugin`` itself does), with each
method's own decorators, parameters and return type, so no moved body changes.
``tests/unit/services/optimal_brain/test_base_index_plugin_mixins.py`` fails on a stub
its surface's class does not define.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from roboco.models.optimal import IndexType, SearchOutcome
    from roboco.services.optimal_brain.indexes.base import IndexConfig, IngestResult
    from roboco.services.optimal_brain.text_chunker import TextChunker
    from roboco.services.optimal_brain.vector_store import VectorStore


class BaseIndexPluginSurface(Protocol):
    """``BaseIndexPlugin``'s members that other classes reach."""

    @property
    def _require_chunker(self) -> TextChunker: ...
    @property
    def _require_embedder(self) -> Any: ...
    @property
    def _require_store(self) -> VectorStore: ...
    @abstractmethod
    def build_source_uri(
        self, doc_id: str | None = ..., **kwargs: Any
    ) -> str | None: ...
    @property
    def config(self) -> IndexConfig: ...
    @property
    @abstractmethod
    def index_type(self) -> IndexType: ...
    @abstractmethod
    def prepare_metadata(self, content: str, **kwargs: Any) -> dict[str, Any]: ...

    replace_on_reingest: bool


class IndexPluginDocumentsSurface(Protocol):
    """``IndexPluginDocuments``'s members that other classes reach."""

    async def count(self) -> int: ...


class IndexPluginIngestionSurface(Protocol):
    """``IndexPluginIngestion``'s members that other classes reach."""

    async def ingest(
        self, content: str, doc_id: str | None = ..., **kwargs: Any
    ) -> IngestResult: ...


class IndexPluginSearchSurface(Protocol):
    """``IndexPluginSearch``'s members that other classes reach."""

    async def search(
        self, query: str, top_k: int = ..., filters: dict[str, Any] | None = ...
    ) -> SearchOutcome: ...


class BaseIndexPluginSelf(
    BaseIndexPluginSurface,
    IndexPluginDocumentsSurface,
    IndexPluginIngestionSurface,
    IndexPluginSearchSurface,
    Protocol,
):
    """Every cross-class member a moved method body reaches via ``self``:
    BaseIndexPlugin's instance attributes here, each class's members on its surface."""

    _chunker: TextChunker | None
    _config: IndexConfig | None
    _embedder: Any
    _initialized: bool
    _store: VectorStore | None
