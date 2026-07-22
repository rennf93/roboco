"""Capture token usage from a Gemini CLI run for the usage / cost dashboard.

Gemini reports its own per-model token stats directly in the run's stdout — no
session-file scraping needed (contrast ``grok_cli_usage``, which has to locate
and parse ``~/.grok/sessions/.../updates.jsonl`` because ``grok -p`` prints no
summary of its own). The Gemini CLI's ``--output-format json`` terminates with
a single ``{response, stats, error?}`` object; ``--output-format stream-json``
(NDJSON: init|message|tool_use|tool_result|error|result) carries the SAME
``stats`` block on its terminal ``result`` event. The entrypoint runs
stream-json (for the live ``docker logs`` view, parity with the Claude/grok
paths) and tees it to a run log this module scans for that ``result`` event.

``stats.models`` is keyed by model name (normally one — the pinned
``ROBOCO_GEMINI_CLI_MODEL`` — but summed generically in case the CLI ever
reports more than one). Its per-model entry shape depends on which
``--output-format`` produced it — our entrypoint always uses stream-json, so
that is the PRIMARY shape parsed; the ``json``-mode shape is a tolerated
fallback (grok-style dual-shape tolerance), never actually hit by our own
entrypoint today:

* **stream-json (primary — what the entrypoint actually emits).** The
  terminal ``result`` event's ``stats`` is ``StreamStats``
  (``packages/core/src/output/types.ts``), built by
  ``stream-json-formatter.ts``'s ``convertToStreamStats``. Each
  ``stats.models.<name>`` entry is FLAT — no nested ``tokens`` key — verbatim:
  ``{total_tokens, input_tokens, output_tokens, cached, input}``, where
  ``input_tokens`` is already the model's full billable prompt-token count
  (``modelMetrics.tokens.prompt``) and ``cached``/``input`` are its own
  breakdown components, not additive on top of it. There is no separate
  "thoughts"/"tool" field in this flat shape — reasoning tokens are simply not
  broken out here, so nothing needs folding in.
* **json mode (fallback — not emitted by our entrypoint).** ``--output-format
  json``'s single-object ``stats`` is the raw ``SessionMetrics``
  (``packages/core/src/telemetry/uiTelemetry.ts``); each
  ``stats.models.<name>`` nests a ``tokens`` sub-object:
  ``{input, prompt, candidates, total, cached, thoughts, tool}``. "Thoughts"
  (reasoning) and tool-use tokens fold into the output bucket here — Gemini
  bills reasoning tokens at the output rate, mirroring how ``grok_cli_usage``
  folds reasoning into output; a cached-content token count folds into input
  (no cached-rate discount is published for Gemini in
  ``roboco.billing.pricing``, so it prices at the full input rate rather than
  an unverified free ride).

Unlike grok (one model, so a blanket total was safe to price at a single
output rate), Gemini's three GA models are priced 4-12x apart, so each
model's tokens are priced with its OWN rate via
``roboco.billing.pricing.calculate_cost`` and the per-model costs are summed
before folding down to the single ``{model, total_tokens, cost_usd}`` shape
the orchestrator reads back (the grok usage.json shape).

This module also classifies the CLI's raw exit code for the entrypoint
(:func:`classify_exit_code`): the Gemini CLI has no dedicated exit code for a
quota/rate-limit error (it falls to the generic 1), so the wrapper remaps it
to 75 by parsing the run's captured JSON for a quota-error ``error.type``
(``TerminalQuotaError`` / ``RetryableQuotaError``) — the one case grok
resolves with a plain text ``grep`` instead, since grok's exit-75 detector has
no verified-error-shape equivalent to key off. Exit 41 (auth) is the CLI's own
dedicated code and passes through unchanged.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from roboco.billing.pricing import calculate_cost

logger = logging.getLogger(__name__)

# Where the entrypoint writes the captured usage for the orchestrator to read.
# Defaults under the system temp dir (not a hardcoded /tmp literal).
USAGE_OUT_PATH = Path(
    os.environ.get("ROBOCO_GEMINI_USAGE_FILE")
    or Path(tempfile.gettempdir()) / "roboco-gemini-usage.json"
)

_DEFAULT_MODEL = "gemini-2.5-pro"

# The Gemini CLI's own quota/rate-limit error.type values (spike-verified).
# Neither maps to a dedicated CLI exit code — both fall to the generic 1 — so
# the entrypoint remaps via classify_exit_code() instead of a text grep.
_QUOTA_ERROR_TYPES = ("TerminalQuotaError", "RetryableQuotaError")
# The CLI's own dedicated auth-failure exit code (source-verified) — passed
# through unchanged by classify_exit_code().
_AUTH_EXIT_CODE = 41
# Remapped target for a quota/rate-limit error (parity with grok's exit-75
# rate-limit detector; see roboco.runtime.orchestrator._GEMINI_RATE_LIMIT_EXIT_CODE).
_RATE_LIMIT_EXIT_CODE = 75


def _coerce_int(value: object) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


def _model_tokens(entry: dict[str, Any]) -> tuple[int, int]:
    """Return ``(input, output)`` tokens for one ``stats.models.<name>`` entry.

    Tries the flat stream-json ``ModelStreamStats`` shape first (our
    entrypoint's real wire format: ``input_tokens``/``output_tokens`` sit
    directly on the entry, no nested key) — a nested ``tokens`` sub-object
    only ever appears on the ``json``-mode ``SessionMetrics`` fallback shape,
    so its presence is the discriminator between the two. See the module
    docstring for the exact field names + source citations for both shapes.
    """
    tokens = entry.get("tokens")
    if isinstance(tokens, dict):
        # Fallback: --output-format json's raw SessionMetrics.ModelMetrics.
        prompt = _coerce_int(tokens.get("prompt", 0))
        cached = _coerce_int(tokens.get("cached", 0))
        candidates = _coerce_int(tokens.get("candidates", 0))
        thoughts = _coerce_int(tokens.get("thoughts", 0))
        tool = _coerce_int(tokens.get("tool", 0))
        return (prompt + cached, candidates + thoughts + tool)
    # Primary: --output-format stream-json's flat ModelStreamStats — the
    # shape our entrypoint actually parses. input_tokens already IS the full
    # billable prompt count (cached is a breakdown component of it, not
    # additive on top).
    return (
        _coerce_int(entry.get("input_tokens", 0)),
        _coerce_int(entry.get("output_tokens", 0)),
    )


def extract_model_stats(stats: dict[str, Any]) -> dict[str, tuple[int, int]]:
    """Return ``{model_name: (input, output)}`` from a run's ``stats`` block.

    ``{}`` for a missing/malformed ``models`` sub-object (no stats parsed at
    all, e.g. a crash before the terminal ``result`` event ever printed).
    """
    models = stats.get("models") if isinstance(stats, dict) else None
    if not isinstance(models, dict):
        return {}
    return {
        str(name): _model_tokens(entry)
        for name, entry in models.items()
        if isinstance(entry, dict)
    }


def usage_and_cost(stats: dict[str, Any]) -> tuple[int, float]:
    """Return ``(total_tokens, total_cost_usd)`` for a run's ``stats`` block.

    Each model's tokens are priced at ITS OWN rate (unlike grok's single-model
    blanket total) and the per-model costs summed.
    """
    total_tokens = 0
    total_cost = 0.0
    for model, (input_tokens, output_tokens) in extract_model_stats(stats).items():
        total_tokens += input_tokens + output_tokens
        total_cost += calculate_cost(
            model, tokens_input=input_tokens, tokens_output=output_tokens
        )
    return total_tokens, round(total_cost, 8)


def _error_type(payload: dict[str, Any]) -> str | None:
    error = payload.get("error")
    if isinstance(error, dict):
        error_type = error.get("type")
        return error_type if isinstance(error_type, str) else None
    return None


def _iter_json_events(text: str) -> list[dict[str, Any]]:
    """Parse *text* as either a single JSON object or NDJSON lines.

    Tolerant of a partially-written / truncated log: unparseable lines are
    skipped rather than aborting the whole scan.
    """
    with contextlib.suppress(json.JSONDecodeError):
        obj = json.loads(text)
        if isinstance(obj, dict):
            return [obj]
    events: list[dict[str, Any]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        with contextlib.suppress(json.JSONDecodeError):
            event = json.loads(line)
            if isinstance(event, dict):
                events.append(event)
    return events


def stats_from_run_log(run_log: Path) -> dict[str, Any]:
    """Extract the ``stats`` object from a Gemini run's captured stdout.

    Handles both ``--output-format json`` (single object) and
    ``--output-format stream-json`` (NDJSON; the terminal ``result`` event
    carries ``stats``, the last one wins). Returns ``{}`` on a missing /
    unparseable / stats-less log.
    """
    try:
        text = run_log.read_text(encoding="utf-8")
    except OSError:
        return {}
    stats: dict[str, Any] = {}
    for event in _iter_json_events(text):
        candidate = event.get("stats")
        if isinstance(candidate, dict) and (
            "type" not in event or event.get("type") == "result"
        ):
            stats = candidate
    return stats


def is_quota_error(run_log: Path) -> bool:
    """True if the run's captured stdout carries a quota-exceeded error.

    Scans for an ``error``-typed NDJSON event (or the single-shot ``error``
    field) whose ``error.type`` is one of :data:`_QUOTA_ERROR_TYPES` — the
    Gemini CLI's own rate-limit/quota-exhaustion signal. The CLI itself exits
    with the generic code 1 for this case, so :func:`classify_exit_code` remaps
    it to 75 instead of falling through to a blind crash-retry.
    """
    try:
        text = run_log.read_text(encoding="utf-8")
    except OSError:
        return False
    return any(
        _error_type(event) in _QUOTA_ERROR_TYPES for event in _iter_json_events(text)
    )


def classify_exit_code(cli_exit_code: int, run_log: Path) -> int:
    """Remap the CLI's raw exit code into RoboCo's provider-park vocabulary.

    41 (auth) is the CLI's own dedicated exit code, passed through unchanged —
    ``roboco.runtime.orchestrator._is_gemini_auth_exit`` checks it directly. A
    quota/rate-limit error has NO dedicated CLI exit code (it falls to the
    generic 1), so this is the one case the wrapper remaps: to 75, parity with
    grok's exit-75 rate-limit detector. Every other exit code (0, 42, 52, 53,
    54, 130, ...) passes through unchanged.
    """
    if cli_exit_code == _AUTH_EXIT_CODE:
        return _AUTH_EXIT_CODE
    if is_quota_error(run_log):
        return _RATE_LIMIT_EXIT_CODE
    return cli_exit_code


def capture_run_usage(*, run_log: Path, fallback_model: str, out_path: Path) -> int:
    """Write ``usage.json`` (``{model, total_tokens, cost_usd}``) for one run.

    Best-effort: a missing/unreadable log or a log with no parsed ``stats``
    still writes a zero-usage file (never raises) — a genuinely absent
    ``usage.json`` at finalize is then unambiguously a mount/path failure, not
    a quiet zero-cost run indistinguishable from "no output happened".
    Returns the total token count written.
    """
    stats = stats_from_run_log(run_log)
    tokens, cost = usage_and_cost(stats)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"model": fallback_model, "total_tokens": tokens, "cost_usd": cost}),
        encoding="utf-8",
    )
    return tokens


def main(argv: list[str] | None = None) -> int:
    """CLI: default writes ``usage.json``; ``--classify-exit`` prints the remapped code.

    The bash entrypoint calls this twice: once (default) after the run to
    capture usage, and once with ``--classify-exit`` to decide what exit code
    to actually return (see :func:`classify_exit_code`). Both read the SAME
    ``ROBOCO_GEMINI_RUN_LOG``.
    """
    args = argv if argv is not None else sys.argv[1:]
    run_log = Path(os.environ.get("ROBOCO_GEMINI_RUN_LOG", ""))

    if "--classify-exit" in args:
        cli_exit_code = int(os.environ.get("ROBOCO_GEMINI_CLI_EXIT_CODE", "1"))
        print(classify_exit_code(cli_exit_code, run_log))
        return 0

    model = os.environ.get("ROBOCO_AGENT_MODEL", _DEFAULT_MODEL)
    capture_run_usage(run_log=run_log, fallback_model=model, out_path=USAGE_OUT_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
