"""Capture token usage from a Codex CLI run for the usage / cost dashboard.

``codex exec --json`` streams typed JSONL to stdout; each completed turn
emits one ``turn.completed`` event carrying a real ``usage`` object —
``{input_tokens, cached_input_tokens, cache_write_input_tokens, output_tokens,
reasoning_output_tokens}``. Unlike grok (a single cumulative total with no
split, folded entirely into the output rate), Codex reports a genuine
input/output/cache split, so this reader prices it properly through
:func:`roboco.billing.pricing.calculate_cost`'s four-bucket formula instead of
grok's output-only fallback.

``cached_input_tokens`` is a SUBSET of ``input_tokens`` (OpenAI's usage
convention: cached tokens are part of the prompt, not additional to it), so
the "fresh" input handed to ``calculate_cost`` is ``input_tokens -
cached_input_tokens`` — treating the full ``input_tokens`` as "fresh" would
double-charge the cached portion at both the full and the cached rate.
``reasoning_output_tokens`` is folded into output (reasoning is billed at the
output rate, the same convention grok's usage reader documents for its own
reasoning tokens).

Multiple ``turn.completed`` events can appear in one run's JSONL (the model
can take more than one turn to finish); this reader sums usage across all of
them, per the build directive to prefer the captured ``--json`` stdout over
the on-disk ``~/.codex/sessions`` rollout files.

The agent entrypoint runs ``python -m roboco.llm.providers.codex_cli_usage``
after the run to write ``usage.json`` (same shape grok_cli_usage produces plus
the real input/output split) into a per-agent dir the orchestrator reads back
at finalize.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from roboco.billing.pricing import calculate_cost

logger = logging.getLogger(__name__)

# Where the entrypoint writes the captured usage for the orchestrator to read.
USAGE_OUT_PATH = Path(
    os.environ.get("ROBOCO_CODEX_USAGE_FILE")
    or Path(tempfile.gettempdir()) / "roboco-codex-usage.json"
)

_TURN_COMPLETED = "turn.completed"
_USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)


def _as_int(value: object) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


def _usage_from_event(event: dict[str, Any]) -> dict[str, int] | None:
    """Pull the raw usage fields from one ``turn.completed`` JSONL event.

    Returns ``None`` for any other event type (``thread.started``,
    ``turn.started``, ``turn.failed``, ``item.*``, ...).
    """
    event_type = event.get("type")
    if event_type != _TURN_COMPLETED:
        return None
    usage = event.get("usage")
    if not isinstance(usage, dict):
        return None
    return {field: _as_int(usage.get(field, 0)) for field in _USAGE_FIELDS}


def aggregate_usage_from_jsonl(run_log: Path) -> dict[str, int]:
    """Sum usage across every ``turn.completed`` event in a captured JSONL log.

    Returns the summed raw fields plus ``turns`` (the ``turn.completed``
    count). Best-effort: a missing/unreadable/empty file returns all zeros —
    usage capture never fails the run.
    """
    totals = dict.fromkeys(_USAGE_FIELDS, 0)
    turns = 0
    try:
        with run_log.open(encoding="utf-8") as fh:
            for raw in fh:
                text = raw.strip()
                if not text:
                    continue
                try:
                    event = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                usage = _usage_from_event(event)
                if usage is None:
                    continue
                turns += 1
                for field in _USAGE_FIELDS:
                    totals[field] += usage[field]
    except OSError:
        pass
    totals["turns"] = turns
    return totals


def usage_and_cost(model: str, agg: dict[str, int]) -> tuple[int, int, int, int, float]:
    """Return ``(input, output, cache_read, cache_write, cost_usd)``.

    ``cached_input_tokens`` is a subset of ``input_tokens`` (not additional),
    so the "fresh" input is the difference; reasoning tokens fold into output.
    """
    cached = agg.get("cached_input_tokens", 0)
    fresh_input = max(0, agg.get("input_tokens", 0) - cached)
    output = agg.get("output_tokens", 0) + agg.get("reasoning_output_tokens", 0)
    cache_write = agg.get("cache_write_input_tokens", 0)
    cost = calculate_cost(
        model,
        tokens_input=fresh_input,
        tokens_output=output,
        tokens_cache_read=cached,
        tokens_cache_write=cache_write,
    )
    return fresh_input, output, cached, cache_write, cost


def capture_run_usage(
    *, run_log: Path, model: str, out_path: Path
) -> tuple[int, int, int, int]:
    """Write ``usage.json`` for one codex run; return the token 4-tuple.

    Best-effort: never raises (returns all zeros and writes nothing on any
    IO/lookup failure).
    """
    try:
        agg = aggregate_usage_from_jsonl(run_log)
        tin, tout, cr, cw, cost = usage_and_cost(model, agg)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(
                {
                    "model": model,
                    "tokens_input": tin,
                    "tokens_output": tout,
                    "tokens_cache_read": cr,
                    "tokens_cache_write": cw,
                    "cost_usd": cost,
                    "turns": agg.get("turns", 0),
                }
            ),
            encoding="utf-8",
        )
        return tin, tout, cr, cw
    except OSError:
        return 0, 0, 0, 0


def main() -> int:
    """Entrypoint: write ``usage.json`` (tokens split + cost) for the run."""
    model = os.environ.get("ROBOCO_AGENT_MODEL", "gpt-5.3-codex")
    run_log = os.environ.get("ROBOCO_CODEX_RUN_LOG", "")
    if not run_log:
        logger.warning("ROBOCO_CODEX_RUN_LOG not set; usage will read 0")
        return 0
    tin, tout, _cr, _cw = capture_run_usage(
        run_log=Path(run_log), model=model, out_path=USAGE_OUT_PATH
    )
    if not tin and not tout:
        logger.warning(
            "codex agent finalized with no readable usage "
            "(0 tokens / $0) — check the run log mount: run_log=%s",
            run_log,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
