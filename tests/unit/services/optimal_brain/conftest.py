"""Conftest for optimal_brain unit tests.

Injects lightweight piragi stubs into sys.modules before any test module is
imported so the index plugins can be imported without the real piragi package
(which requires Ollama, heavy ML dependencies, etc.).
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock


class _StubChunker:
    """Minimal Chunker stub — allows __init__ attribute assignment."""

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def chunk_document(self, *_args: object, **_kwargs: object) -> list[object]:
        return []


def _ensure_piragi_stubbed() -> None:
    """Register stub modules for every piragi sub-package we might import."""
    mock = MagicMock()

    stubs: dict[str, types.ModuleType] = {}

    for name in (
        "piragi",
        "piragi.types",
        "piragi.stores",
        "piragi.stores.postgres",
        "piragi.chunking",
        "piragi.semantic_chunking",
    ):
        if name not in sys.modules:
            mod = types.ModuleType(name)
            # Attach stubs for every attribute that index plugins access
            mod.__dict__["AsyncRagi"] = mock
            mod.__dict__["Citation"] = mock
            mod.__dict__["Document"] = mock
            mod.__dict__["Chunk"] = mock
            mod.__dict__["PostgresStore"] = mock
            # Use a real class so piragi_patches can assign __init__ on it
            mod.__dict__["Chunker"] = _StubChunker
            stubs[name] = mod

    sys.modules.update(stubs)


_ensure_piragi_stubbed()
