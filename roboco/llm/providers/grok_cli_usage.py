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

import contextlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

from roboco.billing.pricing import calculate_cost

logger = logging.getLogger(__name__)

# Where the entrypoint writes the captured usage for the orchestrator to read.
# Defaults under the system temp dir (not a hardcoded /tmp literal).
USAGE_OUT_PATH = Path(
    os.environ.get("ROBOCO_GROK_USAGE_FILE")
    or Path(tempfile.gettempdir()) / "roboco-grok-usage.json"
)


def total_tokens_from_updates(updates_path: Path) -> int:
    """Return the max cumulative ``totalTokens`` in a grok ``updates.jsonl``.

    Each line is a ``session/update`` JSON-RPC event. grok carries the cumulative
    ``totalTokens`` on the inner ``params.update._meta`` (the per-chunk metadata);
    older / alternate shapes put it on ``params._meta`` or the top level. The
    maximum across the file is the session total. Returns 0 for a missing / empty
    / unparseable file (best-effort — usage capture never fails a run).
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
    """Pull the cumulative ``totalTokens`` from a grok ``session/update`` event.

    grok nests the counter on ``params.update._meta`` (the per-chunk metadata);
    ``params._meta`` there only carries event/timing ids. Fall back to
    ``params._meta`` and a top-level field for older / alternate shapes. Returns 0
    when no recognised field is present.
    """
    params = event.get("params") or {}
    update_meta = (params.get("update") or {}).get("_meta") or {}
    params_meta = params.get("_meta") or {}
    for meta in (update_meta, params_meta):
        if isinstance(meta, dict) and "totalTokens" in meta:
            value = meta["totalTokens"]
            return int(value) if isinstance(value, (int, float)) else 0
    value = event.get("totalTokens", 0)
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


def _sid_from_obj(obj: object) -> str | None:
    """A non-empty ``sessionId`` / ``session_id`` from a parsed event, else None."""
    if not isinstance(obj, dict):
        return None
    sid = obj.get("sessionId") or obj.get("session_id")
    return sid if isinstance(sid, str) and sid else None


def session_id_from_run_log(run_log: Path) -> str | None:
    """Read the ``sessionId`` grok generated from the run's output log.

    ``grok -p`` does not honour a requested session id, so the entrypoint hands us
    the run's output and we read the real id back. Handles BOTH the single-object
    ``--output-format json`` log and the NDJSON ``--output-format streaming-json``
    log (the id rides on the terminal ``end`` event). Returns ``None`` for a
    missing / id-less log.
    """
    try:
        text = run_log.read_text(encoding="utf-8")
    except OSError:
        return None
    # A single (possibly pretty-printed multi-line) JSON object first.
    with contextlib.suppress(json.JSONDecodeError):
        sid = _sid_from_obj(json.loads(text))
        if sid:
            return sid
    # Else scan NDJSON lines (streaming-json); the last sessionId wins.
    found: str | None = None
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        with contextlib.suppress(json.JSONDecodeError):
            found = _sid_from_obj(json.loads(stripped)) or found
    return found


def main() -> int:
    """Entrypoint: write ``usage.json`` (model, total_tokens, cost) for the run."""
    cwd = os.environ.get("ROBOCO_GROK_RUN_CWD", str(Path.cwd()))
    model = os.environ.get("ROBOCO_AGENT_MODEL", "grok-build")

    # grok's real session id: from the run's JSON log if the entrypoint passed
    # one (`-s` does not pin the id), else the orchestrator-supplied fallback.
    run_log = os.environ.get("ROBOCO_GROK_RUN_LOG", "")
    parsed_sid = session_id_from_run_log(Path(run_log)) if run_log else None
    if run_log and not parsed_sid:
        # Silent zero-attribution would otherwise hide a mount/path failure as a
        # genuine zero-cost run — keep the swallow, just make it loud.
        logger.warning(
            "ROBOCO_GROK_RUN_LOG set but no session id parsed from run log; "
            "falling back to ROBOCO_AGENT_SESSION_ID (usage may read 0): "
            "run_log=%s",
            run_log,
        )
    session_id = parsed_sid or os.environ.get("ROBOCO_AGENT_SESSION_ID", "")

    capture_session_usage(
        cwd=cwd, session_id=session_id, model=model, out_path=USAGE_OUT_PATH
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
