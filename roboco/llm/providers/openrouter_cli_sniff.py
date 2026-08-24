"""Classify an opencode CLI run's terminal state from ONLY its machine-relevant
output — never the full transcript.

opencode has no documented exit-code taxonomy for ``opencode run`` (every
failure looks the same at the process level). The entrypoint must sniff the
run's output to tell an OpenRouter rate-limit/quota error or an auth failure
apart from any other error. Sniffing the FULL ``--format json`` stream is
unsafe: the model's own on-topic prose can false-positive by construction —
this repo's own role prompts use words like "quota-limited", and a commit hash
or item id can contain the substring "429" or "401"
(:mod:`roboco.llm.providers.kimi_cli_sniff` /
:mod:`roboco.llm.providers.codex_cli_sniff`
document this failure class in detail — this module mirrors their structural
fix).

The fix is structural, not a pattern tweak: extract ONLY a structured
``error`` field off ``type: "error"`` JSONL events (opencode's error-event
shape is ``{"type":"error", "error":{"name":..., "data":{"message":...}}}``
plus a bare-string ``{"error": "..."}`` fallback) and the run's raw stderr,
and sniff THAT text. The model's own echoed assistant/tool content
(``{"type":"text", ...}`` / ``{"type":"tool_call", ...}``) can never reach the
classifier, so it can never trigger a false park by construction.

Patterns (the codex/kimi-proven word-boundaried digit guard, plus
OpenRouter's own error vocabulary):
  - rate-limit: a bare ``\\b429\\b``, "rate limit", "too many requests",
    "overloaded", "quota".
  - auth failure: a bare ``\\b401\\b``, "invalid api key", "unauthorized",
    "authentication".

The entrypoint calls this as ``python -m roboco.llm.providers.openrouter_cli_sniff
<run_log> [err_log]``, printing ``rate_limit`` / ``auth`` / an empty line;
the entrypoint maps rate_limit to exit 75 and auth to exit 78 so the
orchestrator's existing park-and-probe logic (scoped by provider_type) handles
the OpenRouter provider identically to grok/codex/gemini/kimi.
"""

from __future__ import annotations

import contextlib
import json
import re
import sys
from pathlib import Path
from typing import Any

_RATE_LIMIT_PATTERN = re.compile(
    r"(\b429\b|rate limit|too many requests|overloaded|quota)",
    re.IGNORECASE,
)
_AUTH_FAILURE_PATTERN = re.compile(
    r"(\b401\b|invalid api key|unauthorized|authentication)",
    re.IGNORECASE,
)


def _dict_error_text(error: dict[str, Any]) -> str | None:
    """Extract error text from a dict-shaped ``error`` field.

    Handles opencode's ``{"error":{"name":..., "data":{"message":...}}}`` shape
    plus a bare ``{"error":{"message":...}}`` fallback and a ``data`` string.
    """
    parts: list[str] = []
    name = error.get("name")
    if isinstance(name, str) and name:
        parts.append(name)
    data = error.get("data")
    if isinstance(data, dict):
        message = data.get("message")
        if isinstance(message, str) and message:
            parts.append(message)
    elif isinstance(data, str) and data:
        parts.append(data)
    message = error.get("message")
    if isinstance(message, str) and message:
        parts.append(message)
    return " ".join(parts) if parts else None


def _error_text_from_event(event: dict[str, Any]) -> str | None:
    """Pull a structured error message off one JSONL event, or ``None``.

    opencode's ``type: "error"`` event shape is
    ``{"type":"error", "error":{"name":..., "data":{"message":...}}}`` —
    extract ``error.data.message`` and ``error.name`` (the name is an error
    class like ``"RateLimitError"`` / ``"AuthenticationError"`` which also
    classifies correctly). A bare-string ``{"error": "..."}`` fallback is
    tolerated (parity with kimi_cli_sniff). Never reads ``text`` off a
    ``type: "text"`` / ``type: "tool_call"`` event.
    """
    if event.get("type") != "error":
        return None
    error = event.get("error")
    if isinstance(error, dict):
        return _dict_error_text(error)
    if isinstance(error, str) and error:
        return error
    message = event.get("message")
    return message if isinstance(message, str) and message else None


def extract_error_text(run_log: Path) -> str:
    """Pull ONLY structured error text from JSONL events in *run_log*.

    Every other event (``type: text`` / ``type: tool_call`` / ``step_start`` /
    ``step_finish``, ...) is ignored regardless of its content — the model's
    own prose never reaches this text. Best-effort: a missing/unreadable file
    returns "".
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
    429/overload/quota error."""
    return bool(_RATE_LIMIT_PATTERN.search(text))


def is_auth_failure(text: str) -> bool:
    """True if the (already-extracted, machine-only) *text* names an
    auth/key failure."""
    return bool(_AUTH_FAILURE_PATTERN.search(text))


def classify(run_log: Path, err_log: Path | None = None) -> str:
    """Return ``"rate_limit"`` / ``"auth"`` / ``""`` for a captured opencode run.

    Sniffs ONLY the extracted JSONL error text plus the raw stderr — never
    the full stdout transcript (see module docstring).
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
