"""Capture token usage from a Grok CLI session for the usage / cost dashboard.

The grok CLI persists each session under ``~/.grok/sessions/<url-encoded-cwd>/
<session-id>/updates.jsonl``; every update carries a cumulative
``params._meta.totalTokens``, so the maximum across the file is the session's
total token count. This is the grok analogue of the Claude Code transcript —
Grok runs on the SuperGrok subscription, but (exactly like Claude on Max) we
still record per-agent tokens and a notional cost.

The grok CLI reports a single ``totalTokens`` with no input/output split, so the
notional cost prices the whole total at the output rate (the higher rate —
conservative, and consistent with reasoning tokens billing at the output rate).

The agent entrypoint runs ``python -m roboco.llm.providers.grok_cli_usage`` after
the run to write a small ``usage.json`` (``{model, total_tokens, cost_usd}``)
into a per-agent dir the orchestrator reads back at finalize. The interactive
driver reuses :func:`capture_session_usage` directly after each turn (the chat
reuses one grok session id, so the cumulative total is the whole-chat usage).

The session id must be grok's REAL one: ``grok -p`` ignores a requested id (the
``-s`` flag does not pin), so the one-shot entrypoint hands us the run's JSON log
and we read the generated ``sessionId`` out of it (``ROBOCO_GROK_RUN_LOG``),
falling back to ``ROBOCO_AGENT_SESSION_ID`` only when no log is given.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

from roboco.billing.pricing import calculate_cost

# Where the entrypoint writes the captured usage for the orchestrator to read.
USAGE_OUT_PATH = Path(
    os.environ.get("ROBOCO_GROK_USAGE_FILE", "/tmp/roboco-grok-usage.json")
)


def total_tokens_from_updates(updates_path: Path) -> int:
    """Return the max cumulative ``totalTokens`` in a grok ``updates.jsonl``.

    Each line is a ``session/update`` JSON-RPC event whose ``params._meta`` (or a
    top-level field on older formats) carries a cumulative ``totalTokens``. The
    maximum is the session total. Returns 0 for a missing / empty / unparseable
    file (best-effort — usage capture never fails a run).
    """
    best = 0
    try:
        with updates_path.open(encoding="utf-8") as fh:
            for raw in fh:
                text = raw.strip()
                if not text:
                    continue
                try:
                    event = json.loads(text)
                except json.JSONDecodeError:
                    continue
                best = max(best, _extract_total_tokens(event))
    except OSError:
        return 0
    return best


def _extract_total_tokens(event: dict[str, Any]) -> int:
    """Pull ``totalTokens`` from an update event (nested ``_meta`` or top-level)."""
    meta = (event.get("params") or {}).get("_meta") or {}
    value = meta.get("totalTokens", event.get("totalTokens", 0))
    return int(value) if isinstance(value, (int, float)) else 0


def find_updates_path(grok_home: Path, cwd: str, session_id: str) -> Path | None:
    """Locate ``updates.jsonl`` for a session, or ``None`` if not present.

    grok keys the session dir by the url-encoded working directory (``/`` →
    ``%2F``) under ``<grok_home>/sessions/<encoded-cwd>/<session-id>/``.
    """
    if not (cwd and session_id):
        return None
    encoded = quote(cwd, safe="")
    candidate = grok_home / "sessions" / encoded / session_id / "updates.jsonl"
    return candidate if candidate.is_file() else None


def usage_and_cost(model: str, total_tokens: int) -> tuple[int, float]:
    """Return ``(total_tokens, notional_cost_usd)`` for a grok session.

    No input/output split is available, so the whole total is priced at the
    output rate (folded into ``tokens_output``).
    """
    return total_tokens, calculate_cost(
        model, tokens_input=0, tokens_output=total_tokens
    )


def capture_session_usage(
    *,
    cwd: str,
    session_id: str,
    model: str,
    out_path: Path,
    grok_home: Path | None = None,
) -> int:
    """Write ``usage.json`` for one grok session; return its total tokens.

    Reusable by the interactive driver after every turn — the chat reuses one
    session id, so the session store's cumulative ``totalTokens`` is the running
    whole-chat total and the last write wins. Best-effort: never raises (returns
    0 and writes nothing on any IO/lookup failure).
    """
    home = grok_home or Path(os.environ.get("GROK_HOME", str(Path.home() / ".grok")))
    try:
        updates = find_updates_path(home, cwd, session_id)
        total = total_tokens_from_updates(updates) if updates else 0
        tokens, cost = usage_and_cost(model, total)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps({"model": model, "total_tokens": tokens, "cost_usd": cost}),
            encoding="utf-8",
        )
        return tokens
    except OSError:
        return 0


def session_id_from_run_log(run_log: Path) -> str | None:
    """Read the ``sessionId`` grok generated from its ``--output-format json`` log.

    ``grok -p`` does not honour a requested session id, so the entrypoint hands
    us the run's JSON output and we read the real id back. Returns ``None`` for a
    missing / non-JSON / id-less log.
    """
    try:
        payload = json.loads(run_log.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    sid = payload.get("sessionId") or payload.get("session_id")
    return sid if isinstance(sid, str) and sid else None


def main() -> int:
    """Entrypoint: write ``usage.json`` (model, total_tokens, cost) for the run."""
    cwd = os.environ.get("ROBOCO_GROK_RUN_CWD", str(Path.cwd()))
    model = os.environ.get("ROBOCO_AGENT_MODEL", "grok-build")

    # grok's real session id: from the run's JSON log if the entrypoint passed
    # one (`-s` does not pin the id), else the orchestrator-supplied fallback.
    run_log = os.environ.get("ROBOCO_GROK_RUN_LOG", "")
    session_id = (run_log and session_id_from_run_log(Path(run_log))) or os.environ.get(
        "ROBOCO_AGENT_SESSION_ID", ""
    )

    capture_session_usage(
        cwd=cwd, session_id=session_id, model=model, out_path=USAGE_OUT_PATH
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
