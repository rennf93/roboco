"""Cheap per-phase wall-clock timing for gateway verbs.

``timed_phase`` wraps one phase of a verb (a guard, a git push, a forge call)
and emits a single structlog ``verb_phase`` event with ``duration_ms``. It
costs two ``monotonic()`` reads and one log line, so it is safe to leave on
permanently. A phase slower than ``SLOW_PHASE_MS`` logs at warning so a stall
stands out when grepping for ``verb_phase`` by ``level``.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = structlog.get_logger("roboco.verb_timing")

SLOW_PHASE_MS = 5000.0


@contextmanager
def timed_phase(verb: str, phase: str, **fields: Any) -> Iterator[None]:
    """Log ``verb_phase`` with ``duration_ms`` and ``outcome`` on exit.

    Works around ``await`` (a plain sync context manager) and never swallows
    the wrapped block's exception.
    """
    start = time.monotonic()
    outcome = "ok"
    try:
        yield
    except BaseException:
        outcome = "error"
        raise
    finally:
        duration_ms = round((time.monotonic() - start) * 1000.0, 1)
        emit = logger.warning if duration_ms >= SLOW_PHASE_MS else logger.info
        emit(
            "verb_phase",
            verb=verb,
            phase=phase,
            duration_ms=duration_ms,
            outcome=outcome,
            **fields,
        )
