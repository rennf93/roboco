"""Capture token usage from a hummin CLI run for the usage / cost dashboard.

hummin's ``--mode json`` stdout IS the usage source — no session-file
scraping (contrast kimi, whose stdout carries nothing usage-shaped). The
stream is protocol JSONL (ordinary writes are rerouted to stderr by the
output guard): line 1 is the session header, then events. Usage lives on
every assistant ``message_end`` event as
``message.usage = {input, output, cacheRead, cacheWrite, reasoning?,
totalTokens, cost{...}}`` — one record PER API CALL, so a tool loop (N
model calls) emits N assistant ``message_end`` events and the run's true
totals are the SUM across all of them. ``turns`` = the assistant
``message_end`` count.

``input``/``cacheRead``/``cacheWrite`` are already-disjoint buckets (the
pi-ai Usage shape — ``input`` excludes cached tokens), so no subtraction is
needed before pricing (same property as kimi's ``inputOther`` split, unlike
codex's subset-shaped ``cached_input_tokens``).

JSON-mode exit-code trap (spec §5): hummin exits 0 EVEN WHEN the assistant
errored or aborted (the failure arrives as an event with ``stopReason``
``"error"``/``"aborted"``). Usage capture is therefore best-effort and
NEVER the failure signal — the entrypoint consults
:mod:`roboco.llm.providers.hummin_cli_sniff` for the classification.

The agent entrypoint runs ``python -m roboco.llm.providers.hummin_cli_usage``
after the run to write ``usage.json`` (the grok-shaped 4-bucket contract
every CLI provider writes) into the per-agent dir the orchestrator reads
back at finalize. Pricing is the static table
(:func:`roboco.billing.pricing.calculate_cost`); the GLM entries were
re-verified against Z.ai's published prices 2026-09-16. NOTE: no
``glm-5.3-highspeed`` row exists in ``_PRICING`` yet (no verifiable
published rate as of 2026-09-17) — ``calculate_cost`` falls back to the
longest-matching fragment (``glm-5.3``), over-attributing spend at the full
GLM-5.3 rate. Over-attribution is the safe direction; add the row when a
published rate exists.
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
    os.environ.get("ROBOCO_HUMMIN_USAGE_FILE")
    or Path(tempfile.gettempdir()) / "roboco-hummin-usage.json"
)

_DEFAULT_MODEL = "glm-5.3"

# message.usage's per-call buckets, mapped onto the usage.json contract.
_USAGE_BUCKETS = ("input", "output", "cacheRead", "cacheWrite")


def _as_int(value: object) -> int:
    return int(value) if isinstance(value, int | float) else 0


def _assistant_message(event: dict[str, Any]) -> dict[str, Any] | None:
    """The message of a ``message_end`` event, iff it is an assistant message."""
    if event.get("type") != "message_end":
        return None
    message = event.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return None
    return message


def _usage_from_message(message: dict[str, Any]) -> dict[str, int] | None:
    """Pull the four raw usage buckets off one assistant message, or ``None``
    when the event carries no usage object at all."""
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None
    return {bucket: _as_int(usage.get(bucket, 0)) for bucket in _USAGE_BUCKETS}


def _final_text_from_message(message: dict[str, Any]) -> str:
    """The concatenated ``text`` blocks of one assistant message ("" if none)."""
    content = message.get("content")
    if not isinstance(content, list):
        return ""
    parts = [
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and isinstance(block.get("text"), str)
    ]
    return "".join(parts)


def aggregate_usage(run_log: Path) -> dict[str, Any]:
    """Sum ``message.usage`` across ALL assistant ``message_end`` events in a
    captured ``--mode json`` run log.

    Returns the four summed buckets plus ``turns`` (the assistant
    ``message_end`` count) and ``result_text`` (the FINAL assistant
    message's text — the run's answer, per the spec's contract §2).
    Best-effort: a missing/unreadable/empty file returns all zeros.
    """
    totals = dict.fromkeys(_USAGE_BUCKETS, 0)
    turns = 0
    result_text = ""
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
                message = _assistant_message(event)
                if message is None:
                    continue
                usage = _usage_from_message(message)
                if usage is not None:
                    turns += 1
                    for bucket in _USAGE_BUCKETS:
                        totals[bucket] += usage[bucket]
                    result_text = _final_text_from_message(message) or result_text
    except OSError:
        pass
    return {
        "tokens_input": totals["input"],
        "tokens_output": totals["output"],
        "tokens_cache_read": totals["cacheRead"],
        "tokens_cache_write": totals["cacheWrite"],
        "turns": turns,
        "result_text": result_text,
    }


def capture_run_usage(
    *, run_log: Path, model: str, out_path: Path
) -> tuple[int, int, int, int]:
    """Write ``usage.json`` for one hummin run; return the token 4-tuple.

    Best-effort: never raises (returns all zeros and writes nothing on any
    IO failure).
    """
    try:
        agg = aggregate_usage(run_log)
        tin = agg["tokens_input"]
        tout = agg["tokens_output"]
        cache_read = agg["tokens_cache_read"]
        cache_write = agg["tokens_cache_write"]
        cost = calculate_cost(
            model,
            tokens_input=tin,
            tokens_output=tout,
            tokens_cache_read=cache_read,
            tokens_cache_write=cache_write,
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(
                {
                    "model": model,
                    "tokens_input": tin,
                    "tokens_output": tout,
                    "tokens_cache_read": cache_read,
                    "tokens_cache_write": cache_write,
                    "cost_usd": cost,
                    "turns": agg["turns"],
                }
            ),
            encoding="utf-8",
        )
        return tin, tout, cache_read, cache_write
    except OSError:
        return 0, 0, 0, 0


def main() -> int:
    """Entrypoint: write ``usage.json`` (tokens split + cost) for the run."""
    model = os.environ.get("ROBOCO_AGENT_MODEL", _DEFAULT_MODEL)
    run_log = os.environ.get("ROBOCO_HUMMIN_RUN_LOG", "")
    if not run_log:
        logger.warning("ROBOCO_HUMMIN_RUN_LOG not set; usage will read 0")
        return 0
    tin, tout, _cr, _cw = capture_run_usage(
        run_log=Path(run_log), model=model, out_path=USAGE_OUT_PATH
    )
    if not tin and not tout:
        logger.warning(
            "hummin agent finalized with no readable usage (0 tokens / $0) — "
            "check the run log / usage mount: run_log=%s",
            run_log,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
