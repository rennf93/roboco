"""Classify a hummin CLI run's terminal state from ONLY its machine-relevant
output — never the transcript.

hummin's ``--mode json`` has the exit-0-on-error trap (verified: JSON mode
exits 0 EVEN WHEN the assistant errored or aborted — the failure arrives as
an event, not an exit code), so the entrypoint MUST branch on this sniff's
result even when the run "succeeded". Sniffing the full captured stdout is
unsafe by construction: the model's own on-topic prose can false-positive a
raw grep (this repo's own prompts use phrases like "quota-limited", and a
commit hash can contain "429" —
:mod:`roboco.llm.providers.codex_cli_sniff` documents this failure class in
detail; it is the template this module mirrors).

The structural fix, same as codex/kimi: extract ONLY structured fields off
JSONL events — an assistant message's ``stopReason`` / ``errorMessage``
(the verified pi-ai error channel: error/abort termination produces an
AssistantMessage with ``stopReason`` ``"error"``/``"aborted"`` plus
``errorMessage``) and any event-level ``error.message`` — plus the run's
raw stderr, and classify THAT text. The model's own echoed
assistant/tool CONTENT (``content[]`` blocks, tool results) never reaches
the classifier.

Patterns (Z.ai GLM Coding Plan error shapes, same word-boundaried digit
guard as codex/kimi):
  - rate-limit: ``\\b429\\b``, "rate limit", "too many requests", "quota".
  - auth failure: ``\\b401\\b`` / ``\\b403\\b``, "unauthorized",
    "invalid api key", "api key" + "invalid", "authentication".

The entrypoint calls this as ``python -m roboco.llm.providers.hummin_cli_sniff
<run_log> [err_log]``, printing ``rate_limit`` / ``auth`` / an empty line,
and maps the result onto the fleet convention: rate_limit → exit 75
(park), auth → exit 78 (park), otherwise the run's own exit code passes
through.
"""

from __future__ import annotations

import contextlib
import json
import re
import sys
from pathlib import Path
from typing import Any

_RATE_LIMIT_PATTERN = re.compile(
    r"(\b429\b|rate limit|too many requests|\bquota\b)",
    re.IGNORECASE,
)
_AUTH_FAILURE_PATTERN = re.compile(
    r"(\b401\b|\b403\b|unauthorized|invalid api key|invalid key|"
    r"api key is invalid|authentication)",
    re.IGNORECASE,
)


def _error_text_from_assistant_message(message: dict[str, Any]) -> str | None:
    """The verified pi-ai error channel: an assistant message terminated with
    ``stopReason`` ``"error"``/``"aborted"`` carries ``errorMessage``."""
    if message.get("stopReason") not in ("error", "aborted"):
        return None
    error_message = message.get("errorMessage")
    if isinstance(error_message, str) and error_message:
        return error_message
    return f"assistant stopped: {message.get('stopReason')}"


def _error_text_from_event(event: dict[str, Any]) -> str | None:
    """Pull structured error text off one JSONL event, or ``None``.

    Reads ONLY the verified machine channels — an assistant message's
    ``stopReason``/``errorMessage`` pair and a generic ``error.message`` —
    never ``message.content`` (the model's own prose).
    """
    message = event.get("message")
    if isinstance(message, dict) and message.get("role") == "assistant":
        return _error_text_from_assistant_message(message)
    error = event.get("error")
    if isinstance(error, dict):
        inner = error.get("message")
        return inner if isinstance(inner, str) and inner else None
    if isinstance(error, str) and error:
        return error
    return None


def extract_error_text(run_log: Path) -> str:
    """Pull ONLY structured error text from JSONL events in *run_log*.

    Every other event — and every content block of an assistant event — is
    ignored. Best-effort: a missing/unreadable file returns "".
    """
    messages: list[str] = []
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
                message = _error_text_from_event(event)
                if message:
                    messages.append(message)
    except OSError:
        return ""
    return "\n".join(messages)


def is_rate_limited(text: str) -> bool:
    """True if the (already-extracted, machine-only) *text* names a
    Z.ai 429/rate-limit/quota error."""
    return bool(_RATE_LIMIT_PATTERN.search(text))


def is_auth_failure(text: str) -> bool:
    """True if the (already-extracted, machine-only) *text* names an
    auth failure (401/403/invalid key)."""
    return bool(_AUTH_FAILURE_PATTERN.search(text))


def classify(run_log: Path, err_log: Path | None = None) -> str:
    """Return ``"rate_limit"`` / ``"auth"`` / ``""`` for a captured hummin run.

    Sniffs ONLY the extracted JSONL error channels plus the raw stderr —
    never the stdout transcript (see module docstring). Rate-limit wins over
    auth when both appear (the operator-visible cause is the throttle).
    """
    text = extract_error_text(run_log)
    if err_log is not None:
        with contextlib.suppress(OSError):
            text = f"{text}\n{err_log.read_text(encoding='utf-8')}"
    if is_rate_limited(text):
        return "rate_limit"
    if is_auth_failure(text):
        return "auth"
    return ""


def main(argv: list[str] | None = None) -> int:
    """CLI: prints the classification for ``<run_log> [err_log]``."""
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("")
        return 0
    run_log = Path(args[0])
    err_log = Path(args[1]) if len(args) > 1 else None
    print(classify(run_log, err_log))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
