"""Classify a Codex CLI run's terminal state from ONLY its machine-relevant
output — never the full transcript.

Codex has no exit-code taxonomy (every failure exits 1), so the entrypoint
must sniff the run's output to tell an OpenAI rate-limit / auth failure apart
from any other error. Sniffing the FULL captured JSONL stdout is unsafe: the
model's own on-topic prose can false-positive by construction — this repo's
own role prompts use the phrase "quota-limited", a commit hash or item content
can contain the substring "429", ... A prior cut of this entrypoint grepped
the whole transcript and would have false-parked the entire OPENAI provider on
any of that ordinary, on-topic output.

The fix is structural, not a pattern tweak: extract ONLY —
  - the ``error.message`` field of any JSONL event carrying an ``error`` key
    (``turn.failed`` is the documented shape; the check is structural, not
    gated on ``type``, so any other error-bearing event works too), and
  - the run's raw stderr,
and sniff THAT text. The model's own echoed stdout content can never reach
the classifier, so it can never trigger a false park by construction.

Patterns (mirroring grok's own proven, word-boundaried set — see
``docker/scripts/grok-cli-agent-entrypoint.sh``):
  - rate-limit: ``\\b429\\b``, ``rate.?limit``, "too many requests", "quota",
    "insufficient_quota".
  - auth failure: exact phrases only — "refresh token has expired", "not
    signed in". Deliberately NOT the bare word "login" (the panel itself has
    a login page; any transcript mentioning it would false-park the whole
    provider).

The entrypoint calls this as ``python -m roboco.llm.providers.codex_cli_sniff
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
    r"(\b429\b|rate.?limit|too many requests|quota|insufficient_quota)",
    re.IGNORECASE,
)
_AUTH_FAILURE_PATTERN = re.compile(
    r"(refresh token has expired|not signed in)", re.IGNORECASE
)


def extract_error_text(run_log: Path) -> str:
    """Pull ONLY the ``error.message`` text from JSONL events in *run_log*.

    Scans every line for a dict carrying an ``error`` sub-object with a
    ``message`` string (the ``turn.failed`` shape); every other event
    (``turn.completed``, ``item.*``, plain assistant text, ...) is ignored
    regardless of its content — the model's own prose never reaches this
    text. Best-effort: a missing/unreadable file returns "".
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
                error = event.get("error")
                if isinstance(error, dict):
                    message = error.get("message")
                    if isinstance(message, str) and message:
                        messages.append(message)
    except OSError:
        return ""
    return "\n".join(messages)


def is_rate_limited(text: str) -> bool:
    """True if the (already-extracted, machine-only) *text* names a 429/quota error."""
    return bool(_RATE_LIMIT_PATTERN.search(text))


def is_auth_failure(text: str) -> bool:
    """True if the (already-extracted, machine-only) *text* names an auth failure."""
    return bool(_AUTH_FAILURE_PATTERN.search(text))


def classify(run_log: Path, err_log: Path | None = None) -> str:
    """Return ``"rate_limit"`` / ``"auth"`` / ``""`` for a captured Codex run.

    Sniffs ONLY the extracted JSONL ``error.message`` text plus the raw
    stderr — never the full stdout transcript (see module docstring).
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
