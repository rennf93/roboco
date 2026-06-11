"""Claude Code transcript token-usage parsing.

A dependency-light helper (only ``json`` + ``pathlib``) so callers that need
durable token counts — notably the orchestrator's session-finalization path —
can read them without importing the agent SDK server, which pulls in the
FastAPI / RAG (piragi / openai) stack.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path


def _coerce_int(value: Any) -> int:
    """Coerce a transcript usage value to int, treating null/garbage as zero."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _line_usage(line: str) -> tuple[int, int, int, int]:
    """Parse one transcript line into its token deltas.

    Returns ``(input, output, cache_read, cache_write)`` — all zeros for blank
    lines, malformed JSON, or entries without a ``message.usage`` block.
    """
    stripped = line.strip()
    if not stripped:
        return (0, 0, 0, 0)
    try:
        entry = json.loads(stripped)
    except (ValueError, TypeError):
        return (0, 0, 0, 0)
    message = entry.get("message")
    if not isinstance(message, dict):
        return (0, 0, 0, 0)
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return (0, 0, 0, 0)
    return (
        _coerce_int(usage.get("input_tokens")),
        _coerce_int(usage.get("output_tokens")),
        _coerce_int(usage.get("cache_read_input_tokens")),
        _coerce_int(usage.get("cache_creation_input_tokens")),
    )


def sum_transcript_usage(path: Path) -> tuple[int, int, int, int]:
    """Sum per-message token usage across a Claude Code JSONL transcript.

    Each assistant entry carries a ``message.usage`` block with the token
    counts for that API response; summing them yields the session total.
    Returns ``(input, output, cache_read, cache_write)``. Malformed lines are
    skipped — a single bad line must never lose the whole count.
    """
    tin = tout = tcr = tcw = 0
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        for raw in fh:
            line_in, line_out, line_cr, line_cw = _line_usage(raw)
            tin += line_in
            tout += line_out
            tcr += line_cr
            tcw += line_cw
    return tin, tout, tcr, tcw
