"""
Text Chunker — in-house character-based sliding-window chunker.

This module replaces the piragi ``Chunker`` and provides the data-transfer
types (``Chunk``, ``Document``, ``Citation``) that the rest of the optimal
brain stack uses.  **No external tokenizers** are required: chunking is done
by character count only, which is a good approximation for our embedding
model (qwen3-embedding:0.6b) and avoids the trust-remote-code prompt that
the old HuggingFace AutoTokenizer path triggered in non-TTY containers.

Design
------
* ``TextChunker.chunk_document(doc)`` splits a ``Document`` into overlapping
  ``Chunk`` windows of at most *chunk_size* characters.  The split point
  backs up to the nearest whitespace so words are never broken.
* Overlap is subtracted from the start of the next window so consecutive
  chunks share *chunk_overlap* characters of context.
* The minimum useful chunk is 1 character; callers (``base.py``) apply a
  quality filter that drops chunks shorter than 200 characters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Transfer types — replicate the piragi.types surface used by the stack
# ---------------------------------------------------------------------------


@dataclass
class Document:
    """Input document to be chunked and indexed."""

    content: str
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Chunk:
    """A text chunk produced by ``TextChunker``, optionally with an embedding.

    Attributes:
        text:        Raw text of the chunk.
        source:      Source URI (e.g. ``roboco://docs/README.md``).
        chunk_index: Zero-based position of this chunk within its document.
        metadata:    Arbitrary key/value metadata merged in by the plugin.
        embedding:   Float vector populated by the embedder; ``None`` until
                     ``embed_chunks`` has been called.
    """

    text: str
    source: str
    chunk_index: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: list[float] | None = None


@dataclass
class Citation:
    """A search result returned by ``VectorStore.search``.

    Attributes:
        chunk:    Text content of the matched chunk.
        source:   Source URI of the document that produced this chunk.
        score:    Cosine similarity score in [0, 1] (higher = more similar).
        metadata: Metadata stored alongside the chunk.
    """

    chunk: str
    source: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Chunker
# ---------------------------------------------------------------------------

# Whitespace characters treated as legal split points.
_SPLIT_CHARS = frozenset(" \t\n\r")


class TextChunker:
    """Character-based sliding-window chunker.

    Parameters
    ----------
    chunk_size:
        Maximum number of characters in a single chunk.  Defaults to 512.
    chunk_overlap:
        Number of characters from the end of one chunk to repeat at the
        start of the next.  Defaults to 128.  Must be < ``chunk_size``.

    Usage
    -----
    >>> chunker = TextChunker(chunk_size=512, chunk_overlap=128)
    >>> doc = Document(content="long text ...", source="roboco://docs/x")
    >>> chunks = chunker.chunk_document(doc)
    """

    def __init__(
        self,
        chunk_size: int = 512,
        chunk_overlap: int = 128,
    ) -> None:
        if chunk_overlap >= chunk_size:
            raise ValueError(
                f"chunk_overlap ({chunk_overlap}) must be less than "
                f"chunk_size ({chunk_size})"
            )
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def chunk_document(self, doc: Document) -> list[Chunk]:
        """Split *doc* into overlapping character-window chunks.

        Args:
            doc: The document to chunk.

        Returns:
            Ordered list of ``Chunk`` objects; at minimum one chunk even for
            very short documents.
        """
        return self.chunk_text(doc.content, doc.source)

    def chunk_text(self, text: str, source: str) -> list[Chunk]:
        """Split *text* into overlapping character-window chunks.

        If the text fits within a single window the result is a single chunk.
        Otherwise chunks are produced with a sliding window that backs up to
        the nearest whitespace boundary to avoid cutting words.

        Args:
            text:   Raw text to chunk.
            source: Source URI stored on every resulting ``Chunk``.

        Returns:
            Ordered list of ``Chunk`` objects.
        """
        if not text:
            return []

        if len(text) <= self.chunk_size:
            return [
                Chunk(
                    text=text,
                    source=source,
                    chunk_index=0,
                    metadata={},
                )
            ]

        chunks: list[Chunk] = []
        start = 0
        chunk_index = 0

        while start < len(text):
            raw_end = min(start + self.chunk_size, len(text))

            # If we haven't reached the end of the text, back up to a
            # whitespace boundary so we don't split in the middle of a word.
            end = self._find_split_point(text, start, raw_end)

            chunk_text = text[start:end].strip()
            if chunk_text:
                chunks.append(
                    Chunk(
                        text=chunk_text,
                        source=source,
                        chunk_index=chunk_index,
                        metadata={},
                    )
                )
                chunk_index += 1

            # Advance the window, keeping `chunk_overlap` chars from the
            # current chunk so adjacent chunks share context.
            next_start = end - self.chunk_overlap
            if next_start <= start:
                # Guard against infinite loops on degenerate input.
                next_start = start + max(1, self.chunk_size - self.chunk_overlap)
            start = next_start

        return chunks

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _find_split_point(self, text: str, start: int, raw_end: int) -> int:
        """Return a split index ≤ *raw_end* that falls on a whitespace char.

        Searches backwards from *raw_end* to find a whitespace boundary.
        Falls back to *raw_end* if no whitespace is found in the lower half
        of the window (prevents excessively tiny chunks).
        """
        if raw_end >= len(text):
            return raw_end

        # Look back for a split point, but not further than halfway into the
        # current window (to keep chunks reasonably large).
        min_search = start + self.chunk_size // 2
        pos = raw_end
        while pos > min_search and text[pos] not in _SPLIT_CHARS:
            pos -= 1

        if text[pos] in _SPLIT_CHARS:
            return pos  # split just before the whitespace character

        # No whitespace found — hard-cut at raw_end.
        return raw_end
