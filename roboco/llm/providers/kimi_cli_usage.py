"""Capture token usage from a Kimi CLI run for the usage / cost dashboard.

Unlike codex/gemini (usage summarized directly in their own captured
stdout), kimi reports NOTHING usage-shaped in ``kimi -p --output-format
stream-json``'s stdout — only assistant/tool messages and a terminal
``{"role": "meta", "type": "session.resume_hint", "session_id": ...}`` line.
The real usage lives on disk, per session, at
``$KIMI_CODE_HOME/sessions/<workDirKey>/<sessionId>/agents/main/wire.jsonl``
(``workDirKey`` = ``wd_<cwd-basename>_<hash12>``, live-verified) as
``{"type": "usage.record", "model": ..., "usageScope": "turn", "usage":
{"inputOther", "output", "inputCacheRead", "inputCacheCreation"}}`` events —
a genuine 4-bucket split (unlike grok's output-only fallback; parity with
codex's real input/output/cache split, see
:mod:`roboco.llm.providers.codex_cli_usage`, the template this module
mirrors for the 4-bucket ``usage.json`` write).

Session id resolution: the PRIMARY source is the run's own captured stdout
(the terminal ``session.resume_hint`` line) — no file scraping for the id
itself, unlike grok. When that's absent (a crashed run that never reached
the terminal event), the FALLBACK is the newest session directory under the
workdir-keyed ``sessions/wd_<cwd-basename>_*/`` glob — the exact hash suffix
of ``workDirKey`` isn't reproducible without the CLI's own hash function, so
this globs on the cwd-basename prefix instead of computing it.

``inputOther``/``inputCacheRead``/``inputCacheCreation`` are already
disjoint buckets (unlike codex's ``cached_input_tokens``, which is a SUBSET
of ``input_tokens``) — the field name ``inputOther`` ("input, other than
cached") is deliberately not-"input", so no subtraction is needed before
pricing.

The agent entrypoint runs ``python -m roboco.llm.providers.kimi_cli_usage``
after the run to write ``usage.json`` (the same grok-shaped 4-bucket shape
codex writes) into a per-agent dir the orchestrator reads back at finalize.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from roboco.billing.pricing import calculate_cost

logger = logging.getLogger(__name__)

# Where the entrypoint writes the captured usage for the orchestrator to read.
USAGE_OUT_PATH = Path(
    os.environ.get("ROBOCO_KIMI_USAGE_FILE")
    or Path(tempfile.gettempdir()) / "roboco-kimi-usage.json"
)

# kimi's global state dir (see roboco.llm.providers.kimi_cli_config).
KIMI_CODE_HOME = Path.home() / ".kimi-code"

_DEFAULT_MODEL = "kimi-code/k3"
_USAGE_RECORD_TYPE = "usage.record"
_TURN_SCOPE = "turn"
_USAGE_FIELDS = ("inputOther", "output", "inputCacheRead", "inputCacheCreation")


def _as_int(value: object) -> int:
    return int(value) if isinstance(value, int | float) else 0


def _session_id_from_event(event: dict) -> str | None:
    """Pull the session id off one ``session.resume_hint`` meta event, or
    ``None`` for any other event type."""
    if event.get("role") != "meta" or event.get("type") != "session.resume_hint":
        return None
    sid = event.get("session_id")
    return sid if isinstance(sid, str) and sid else None


def session_id_from_run_log(run_log: Path) -> str | None:
    """Extract the session id from the run's terminal ``session.resume_hint``
    meta line. Returns the LAST matching line's id (a crashed/resumed run
    could in principle emit more than one); ``None`` on a missing/unreadable/
    id-less log."""
    found: str | None = None
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
                sid = _session_id_from_event(event)
                if sid:
                    found = sid
    except OSError:
        return None
    return found


def _cwd_basename(workdir: str) -> str:
    return Path(workdir).name or "workspace"


def _find_session_by_id(
    sessions_root: Path, workdir_pattern: str, session_id: str
) -> Path | None:
    """The known ``session_id`` under any ``wd_<basename>_*`` workdir key
    (the hash suffix isn't reproducible client-side, so this globs the
    prefix)."""
    matches = list(sessions_root.glob(f"{workdir_pattern}/{session_id}"))
    return matches[0] if matches else None


def _newest_session_dir(sessions_root: Path, workdir_pattern: str) -> Path | None:
    """The most-recently-modified session dir under any matching workdir key."""
    candidates = [
        session_dir
        for wd_dir in sessions_root.glob(workdir_pattern)
        if wd_dir.is_dir()
        for session_dir in wd_dir.iterdir()
        if session_dir.is_dir()
    ]
    return max(candidates, key=lambda d: d.stat().st_mtime) if candidates else None


def resolve_session_dir(
    *,
    session_id: str | None,
    workdir: str,
    kimi_code_home: Path = KIMI_CODE_HOME,
) -> Path | None:
    """Locate the session directory for this run.

    Primary: :func:`_find_session_by_id`. Fallback: :func:`_newest_session_dir`.
    Returns ``None`` when nothing matches.
    """
    sessions_root = kimi_code_home / "sessions"
    if not sessions_root.is_dir():
        return None
    workdir_pattern = f"wd_{_cwd_basename(workdir)}_*"
    if session_id:
        found = _find_session_by_id(sessions_root, workdir_pattern, session_id)
        if found is not None:
            return found
    return _newest_session_dir(sessions_root, workdir_pattern)


def _usage_from_wire_event(event: dict) -> dict[str, int] | None:
    """Pull the raw usage fields off one turn-scoped ``usage.record`` wire
    event, or ``None`` for any other event (``llm.request``, a session-scoped
    record, ...)."""
    if event.get("type") != _USAGE_RECORD_TYPE or event.get("usageScope") != (
        _TURN_SCOPE
    ):
        return None
    usage = event.get("usage")
    if not isinstance(usage, dict):
        return None
    return {field: _as_int(usage.get(field, 0)) for field in _USAGE_FIELDS}


def aggregate_usage_from_wire(wire_log: Path) -> dict[str, int]:
    """Sum ``usage.record`` (``usageScope == "turn"``) events in a session's
    ``wire.jsonl``. Returns the summed raw fields plus ``turns`` (the event
    count). Best-effort: a missing/unreadable/empty file returns all zeros.
    """
    totals = dict.fromkeys(_USAGE_FIELDS, 0)
    turns = 0
    try:
        with wire_log.open(encoding="utf-8") as fh:
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
                usage = _usage_from_wire_event(event)
                if usage is None:
                    continue
                turns += 1
                for field in _USAGE_FIELDS:
                    totals[field] += usage[field]
    except OSError:
        pass
    totals["turns"] = turns
    return totals


def capture_run_usage(
    *,
    run_log: Path,
    workdir: str,
    model: str,
    out_path: Path,
    kimi_code_home: Path = KIMI_CODE_HOME,
) -> tuple[int, int, int, int]:
    """Write ``usage.json`` for one kimi run; return the token 4-tuple.

    Best-effort: never raises (returns all zeros and writes nothing on any
    IO/lookup failure).
    """
    try:
        session_id = session_id_from_run_log(run_log)
        session_dir = resolve_session_dir(
            session_id=session_id, workdir=workdir, kimi_code_home=kimi_code_home
        )
        agg: dict[str, int] = (
            aggregate_usage_from_wire(session_dir / "agents" / "main" / "wire.jsonl")
            if session_dir is not None
            else {**dict.fromkeys(_USAGE_FIELDS, 0), "turns": 0}
        )
        tin = agg["inputOther"]
        tout = agg["output"]
        cache_read = agg["inputCacheRead"]
        cache_write = agg["inputCacheCreation"]
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
                    "turns": agg.get("turns", 0),
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
    run_log = os.environ.get("ROBOCO_KIMI_RUN_LOG", "")
    workdir = os.environ.get("ROBOCO_KIMI_WORKDIR", "")
    if not run_log:
        logger.warning("ROBOCO_KIMI_RUN_LOG not set; usage will read 0")
        return 0
    # kimi_code_home passed explicitly (not relying on capture_run_usage's own
    # default, which binds at function-definition time and would go stale if
    # a caller reassigns the module global after import — a real gap for e.g.
    # a test module monkeypatching it post-import).
    tin, tout, _cr, _cw = capture_run_usage(
        run_log=Path(run_log),
        workdir=workdir,
        model=model,
        out_path=USAGE_OUT_PATH,
        kimi_code_home=KIMI_CODE_HOME,
    )
    if not tin and not tout:
        logger.warning(
            "kimi agent finalized with no readable usage "
            "(0 tokens / $0) — check the sessions mount / workdir env: "
            "run_log=%s workdir=%s",
            run_log,
            workdir,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
