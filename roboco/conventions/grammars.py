"""Lazy tree-sitter parser construction, one per language.

Grammars are loaded on first use and cached. A missing grammar raises
``GrammarUnavailable`` so the runner can fail loud (the gate blocks with
"validator could not run" rather than silently passing).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Language, Parser


class GrammarUnavailable(RuntimeError):
    """Raised when a language's tree-sitter grammar cannot be loaded."""

    def __init__(self, language: str) -> None:
        super().__init__(f"tree-sitter grammar unavailable for {language!r}")
        self.language = language


_PARSERS: dict[str, Parser] = {}


def _load_language(language: str) -> Language:
    from tree_sitter import Language

    try:
        if language == "python":
            import tree_sitter_python

            return Language(tree_sitter_python.language())
        if language == "typescript":
            import tree_sitter_typescript

            return Language(tree_sitter_typescript.language_typescript())
        if language == "tsx":
            import tree_sitter_typescript

            return Language(tree_sitter_typescript.language_tsx())
    except ImportError as exc:
        raise GrammarUnavailable(language) from exc
    raise GrammarUnavailable(language)


def get_parser(language: str) -> Parser:
    """Return a cached parser for ``language`` (``python``/``typescript``/``tsx``)."""
    cached = _PARSERS.get(language)
    if cached is not None:
        return cached
    from tree_sitter import Parser

    parser = Parser(_load_language(language))
    _PARSERS[language] = parser
    return parser
