"""Source-expansion behavior for the documentation index plugin.

Locks the glob / directory / single-file resolution paths so the
complexity-driven extraction into ``_expand_directory`` / ``_expand_file``
stays behavior-preserving.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from roboco.services.optimal_brain.indexes.docs import DocsIndexPlugin

if TYPE_CHECKING:
    from pathlib import Path


def _plugin() -> DocsIndexPlugin:
    return DocsIndexPlugin()


def test_expand_single_markdown_file(tmp_path: Path) -> None:
    doc = tmp_path / "guide.md"
    doc.write_text("# hi", encoding="utf-8")
    assert _plugin()._expand_source(str(doc)) == [doc]


def test_expand_single_text_file(tmp_path: Path) -> None:
    doc = tmp_path / "notes.txt"
    doc.write_text("hi", encoding="utf-8")
    assert _plugin()._expand_source(str(doc)) == [doc]


def test_expand_non_doc_file_is_skipped(tmp_path: Path) -> None:
    src = tmp_path / "component.tsx"
    src.write_text("export {}", encoding="utf-8")
    assert _plugin()._expand_source(str(src)) == []


def test_expand_missing_path_is_empty(tmp_path: Path) -> None:
    assert _plugin()._expand_source(str(tmp_path / "nope.md")) == []


def test_expand_directory_collects_md_and_txt(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("a", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    (tmp_path / "c.tsx").write_text("c", encoding="utf-8")
    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / "d.md").write_text("d", encoding="utf-8")

    found = {p.name for p in _plugin()._expand_source(str(tmp_path))}

    assert found == {"a.md", "b.txt", "d.md"}


def test_expand_directory_skips_ignored_dirs(tmp_path: Path) -> None:
    (tmp_path / "keep.md").write_text("k", encoding="utf-8")
    ignored = tmp_path / "node_modules"
    ignored.mkdir()
    (ignored / "dep.md").write_text("x", encoding="utf-8")

    found = {p.name for p in _plugin()._expand_source(str(tmp_path))}

    assert found == {"keep.md"}
