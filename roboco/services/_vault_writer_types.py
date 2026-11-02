"""Structural type for the composed VaultWriter, TYPE_CHECKING-only.

``VaultWriter`` is composed from mixins (``roboco/services/vault_writer.py``) whose
methods were moved verbatim out of the one class. mypy checks each mixin against its own
bases only, so a call into a sibling mixin, or into VaultWriter's own members, would not
resolve.

Each mixin gives its class a TYPE_CHECKING-only base of ``VaultWriterSelf``, which
declares that cross-class surface: ``FooSurface`` holds what ``Foo`` offers the other
classes (``VaultWriterSurface`` what ``VaultWriter`` itself does), with each method's
own decorators, parameters and return type, so no moved body changes.
``tests/unit/services/test_vault_writer_mixins.py`` fails on a stub its surface's class
does not define.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path


class VaultWriterSurface(Protocol):
    """``VaultWriter``'s members that other classes reach."""

    def _a2a_root(self) -> Path: ...
    def _agents_root(self) -> Path: ...
    def _archive_root(self) -> Path: ...
    def _journals_root(self) -> Path: ...
    def _reports_root(self) -> Path: ...
    def _tasks_root(self) -> Path: ...


class VaultWriterSelf(VaultWriterSurface, Protocol):
    """Every cross-class member a moved method body reaches via ``self``: VaultWriter's
    instance attributes here, each class's members on its surface."""

    root: Path
