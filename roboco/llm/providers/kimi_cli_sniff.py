"""Classify a Kimi CLI run's terminal state from ONLY its machine-relevant
output — never the full transcript.

Kimi has no documented exit-code taxonomy for ``kimi -p`` (a claimed 75/1
split is unverified and suspiciously matches RoboCo's own park-exit
convention — treated as noise, not a real CLI contract). The entrypoint must
therefore sniff the run's output to tell a Moonshot rate-limit/quota error or
a membership/auth failure apart from any other error. Sniffing the FULL
captured stream-json stdout is unsafe: the model's own on-topic prose can
false-positive by construction — this repo's own role prompts use words like
"quota-limited", and a commit hash or item id can contain the substring
"429" (:mod:`roboco.llm.providers.codex_cli_sniff` documents this failure
class in detail — it is the template this module mirrors).

The fix is structural, not a pattern tweak: extract ONLY a structured
``error`` field off any JSONL event that carries one (whichever of the few
plausible shapes it takes — a nested ``{"error": {"message": ...}}``, a bare
``{"error": "..."}`` string, or an ``{"type"/"role": "error", "message":
...}`` event) plus the run's raw stderr, and sniff THAT text. The model's own
echoed assistant/tool content (``{"role": "assistant", ...}`` /
``{"role": "tool", ...}``) can never reach the classifier, so it can never
trigger a false park by construction.

Patterns (live-verified error text from the spike, plus the codex-proven
word-boundaried digit guard):
  - rate-limit: "status code: 429", a bare ``\\b429\\b``, "engine is
    currently overloaded", "usage limit for this period" / "usage limit for
    this billing cycle" (a 403 quota-exhaustion, classified as rate_limit
    per the same "try again later" semantics as a 429).
  - auth failure: "API Key appears to be invalid" (401), "unable to verify
    your membership benefits" (the live-verified subscription-gate message),
    a bare ``\\b401\\b``.

The entrypoint calls this as ``python -m roboco.llm.providers.kimi_cli_sniff
<run_log> [err_log]``, printing ``rate_limit`` / ``auth`` / an empty line.
"""

from __future__ import annotations

import contextlib
import json
import re
import sys
from pathlib import Path
from typing import Any

_RATE_LIMIT_PATTERN = re.compile(
    r"(status code:\s*429|\b429\b|engine is currently overloaded|"
    r"usage limit for this (?:period|billing cycle))",
    re.IGNORECASE,
)
_AUTH_FAILURE_PATTERN = re.compile(
    r"(api key appears to be invalid|"
    r"unable to verify your membership benefits|\b401\b)",
    re.IGNORECASE,
)


def _error_text_from_event(event: dict[str, Any]) -> str | None:
    """Pull a structured error message off one JSONL event, or ``None``.

    Tolerates the few plausible shapes an error-bearing event could take
    (kimi's real error-event schema wasn't pinned down beyond the raw
    membership/rate-limit message text itself) — never reads
    ``content``/``text`` off an ``assistant``/``tool`` event.
    """
    error = event.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        return message if isinstance(message, str) and message else None
    if isinstance(error, str) and error:
        return error
    if event.get("type") == "error" or event.get("role") == "error":
        message = event.get("message")
        return message if isinstance(message, str) and message else None
    return None


def extract_error_text(run_log: Path) -> str:
    """Pull ONLY structured error text from JSONL events in *run_log*.

    Every other event (``role: assistant`` / ``role: tool`` / the terminal
    ``role: meta`` line, ...) is ignored regardless of its content — the
    model's own prose never reaches this text. Best-effort: a missing/
    unreadable file returns "".
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
    429/overload/usage-limit error."""
    return bool(_RATE_LIMIT_PATTERN.search(text))


def is_auth_failure(text: str) -> bool:
    """True if the (already-extracted, machine-only) *text* names an
    auth/membership failure."""
    return bool(_AUTH_FAILURE_PATTERN.search(text))


def classify(run_log: Path, err_log: Path | None = None) -> str:
    """Return ``"rate_limit"`` / ``"auth"`` / ``""`` for a captured Kimi run.

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
