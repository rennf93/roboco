"""Claude Code transcript token-usage parsing.

A dependency-light helper (only ``json`` + ``pathlib``) so callers that need
durable token counts — notably the orchestrator's session-finalization path —
can read them without importing the agent SDK server, which pulls in the
FastAPI / RAG (in-house pgvector / openai) stack.
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


def _line_usage(line: str) -> tuple[str | None, tuple[int, int, int, int]] | None:
    """Parse one transcript line into ``(message_id, token deltas)``.

    Returns ``None`` for blank lines, malformed JSON, or entries without a
    ``message.usage`` block. ``message_id`` lets the caller de-duplicate:
    Claude Code logs a single assistant message as several lines (one per
    content block — thinking, text, tool_use), each repeating the *same*
    ``usage``, so counting every line would multiply the totals.
    """
    stripped = line.strip()
    if not stripped:
        return None
    try:
        entry = json.loads(stripped)
    except (ValueError, TypeError):
        return None
    message = entry.get("message")
    if not isinstance(message, dict):
        return None
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None
    deltas = (
        _coerce_int(usage.get("input_tokens")),
        _coerce_int(usage.get("output_tokens")),
        _coerce_int(usage.get("cache_read_input_tokens")),
        _coerce_int(usage.get("cache_creation_input_tokens")),
    )
    return message.get("id"), deltas


def sum_transcript_usage(path: Path) -> tuple[int, int, int, int, int]:
    """Sum per-message token usage + turn count across a Claude Code transcript.

    Each assistant message carries a ``message.usage`` block with the token
    counts for that API response; summing them yields the session total.
    Messages that span several lines (same ``message.id``) are counted once —
    Claude Code emits one line per content block, each repeating the message's
    usage, so naive summing roughly doubles the totals. Returns
    ``(input, output, cache_read, cache_write, turns)`` where ``turns`` is the
    number of UNIQUE assistant ``message.id``s (i.e. LLM iterations). Malformed
    lines are skipped — a single bad line must never lose the whole count.
    """
    tin = tout = tcr = tcw = 0
    seen: set[str] = set()
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        for raw in fh:
            parsed = _line_usage(raw)
            if parsed is None:
                continue
            message_id, (line_in, line_out, line_cr, line_cw) = parsed
            if message_id is not None:
                if message_id in seen:
                    continue
                seen.add(message_id)
            tin += line_in
            tout += line_out
            tcr += line_cr
            tcw += line_cw
    return tin, tout, tcr, tcw, len(seen)
