"""Capture token usage + cost from an opencode CLI run for the usage dashboard.

opencode's ``run --format json`` streams JSONL events to stdout. Unlike kimi
(which reports nothing usage-shaped in stdout and requires a session-dir
wire.jsonl lookup), opencode puts the usage inline in each ``step_finish``
event:

    {"type":"step_finish", ..., "part": {..., "type":"step-finish",
     "tokens": {"total":7583, "input":204, "output":13, "reasoning":6,
                "cache":{"write":0,"read":7360}},
     "cost":0.0}}

This module sums the ``step_finish`` events' ``part.tokens`` and
``part.cost`` across the whole run and writes the grok-shaped 4-bucket
``usage.json`` the orchestrator reads back at finalize.

**Cost comes from OpenRouter's metered ``cost`` field, NOT the static
``_PRICING`` table.** OpenRouter charges per-request and reports the charge
in the event; RoboCo attributes spend directly from that field
(:data:`roboco.billing.pricing._PRICING` has NO OpenRouter rows by design —
the live-catalog provider's spend is always the metered figure).

The 4-bucket mapping (parity with kimi/codex's usage.json shape):
  * ``tokens_input``      = ``tokens.input``       (non-cached prompt tokens)
  * ``tokens_output``     = ``tokens.output`` + ``tokens.reasoning``
    (completion incl. reasoning — OpenRouter prices reasoning as completion)
  * ``tokens_cache_read``  = ``tokens.cache.read``
  * ``tokens_cache_write`` = ``tokens.cache.write``

The agent entrypoint runs ``python -m roboco.llm.providers.openrouter_cli_usage``
after the run to write ``usage.json`` into the per-agent dir the orchestrator
reads back at finalize.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Where the entrypoint writes the captured usage for the orchestrator to read.
USAGE_OUT_PATH = Path(
    os.environ.get("ROBOCO_OPENROUTER_USAGE_FILE")
    or Path(tempfile.gettempdir()) / "roboco-openrouter-usage.json"
)

_DEFAULT_MODEL = "anthropic/claude-sonnet-4"
_STEP_FINISH = "step_finish"


def _as_float(value: object) -> float:
    return float(value) if isinstance(value, int | float) else 0.0


def _as_int(value: object) -> int:
    return int(value) if isinstance(value, int | float) else 0


def _tokens_from_step_finish(event: dict[str, Any]) -> dict[str, int] | None:
    """Pull the raw token fields off one ``step_finish`` event, or ``None``.

    The tokens live under ``event["part"]["tokens"]``; the shape is
    ``{total, input, output, reasoning, cache: {read, write}}``. Any other
    event type (``step_start``, ``text``, ``error``, ...) returns None.
    """
    if event.get("type") != _STEP_FINISH:
        return None
    part = event.get("part")
    if not isinstance(part, dict):
        return None
    tokens = part.get("tokens")
    if not isinstance(tokens, dict):
        return None
    cache = tokens.get("cache") or {}
    return {
        "input": _as_int(tokens.get("input", 0)),
        "output": _as_int(tokens.get("output", 0)),
        "reasoning": _as_int(tokens.get("reasoning", 0)),
        "cache_read": _as_int(cache.get("read", 0)),
        "cache_write": _as_int(cache.get("write", 0)),
    }


def _cost_from_step_finish(event: dict[str, Any]) -> float | None:
    """Pull the metered cost off one ``step_finish`` event, or ``None``.

    The cost lives under ``event["part"]["cost"]`` (OpenRouter's metered
    charge for that step). Returns None for non-step-finish events.
    """
    if event.get("type") != _STEP_FINISH:
        return None
    part = event.get("part")
    if not isinstance(part, dict):
        return None
    return _as_float(part.get("cost", 0.0))


def aggregate_usage(run_log: Path) -> dict[str, Any]:
    """Sum ``step_finish`` events in *run_log* (the ``--format json`` stream).

    Returns the summed 4-bucket tokens, ``turns`` (the step_finish count), and
    ``cost_usd`` (the summed metered cost). Best-effort: a missing/unreadable/
    empty file returns all zeros.
    """
    totals = {
        "input": 0,
        "output": 0,
        "reasoning": 0,
        "cache_read": 0,
        "cache_write": 0,
    }
    cost = 0.0
    turns = 0
    try:
        with run_log.open(encoding="utf-8") as fh:
            for raw in fh:
                text = raw.strip()
                if not text:
                    continue
                try:
                    event: Any = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                tokens = _tokens_from_step_finish(event)
                if tokens is None:
                    continue
                turns += 1
                for field in totals:
                    totals[field] += tokens[field]
                step_cost = _cost_from_step_finish(event)
                if step_cost is not None:
                    cost += step_cost
    except OSError:
        pass
    return {
        "input": totals["input"],
        "output": totals["output"] + totals["reasoning"],
        "cache_read": totals["cache_read"],
        "cache_write": totals["cache_write"],
        "turns": turns,
        "cost_usd": cost,
    }


def capture_run_usage(
    *,
    run_log: Path,
    model: str,
    out_path: Path,
) -> tuple[int, int, int, int]:
    """Write ``usage.json`` for one opencode run; return the token 4-tuple.

    Best-effort: never raises (returns all zeros and writes nothing on any IO
    failure). The cost comes from the metered ``cost`` field, NOT
    :func:`roboco.billing.pricing.calculate_cost` — OpenRouter has no
    ``_PRICING`` row by design.
    """
    if not run_log.is_file():
        return 0, 0, 0, 0
    try:
        agg = aggregate_usage(run_log)
        tin = agg["input"]
        tout = agg["output"]
        cache_read = agg["cache_read"]
        cache_write = agg["cache_write"]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(
                {
                    "model": model,
                    "tokens_input": tin,
                    "tokens_output": tout,
                    "tokens_cache_read": cache_read,
                    "tokens_cache_write": cache_write,
                    "cost_usd": agg["cost_usd"],
                    "turns": agg["turns"],
                }
            ),
            encoding="utf-8",
        )
        return tin, tout, cache_read, cache_write
    except OSError:
        return 0, 0, 0, 0


def main() -> int:
    """Entrypoint: write ``usage.json`` (tokens split + metered cost) for the run."""
    model = os.environ.get("ROBOCO_AGENT_MODEL", _DEFAULT_MODEL)
    run_log = os.environ.get("ROBOCO_OPENROUTER_RUN_LOG", "")
    if not run_log:
        logger.warning("ROBOCO_OPENROUTER_RUN_LOG not set; usage will read 0")
        return 0
    tin, tout, _cr, _cw = capture_run_usage(
        run_log=Path(run_log),
        model=model,
        out_path=USAGE_OUT_PATH,
    )
    if not tin and not tout:
        logger.warning(
            "opencode agent finalized with no readable usage "
            "(0 tokens / $0) — check the run log path: %s",
            run_log,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
