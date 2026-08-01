"""Bounded advisory-evidence legs for claim_review / claim_doc_task /
claim_gate_review / evidence() / i_am_done's success envelope.

Claim-time (and i_am_done's post-transition) evidence assembly is advisory,
not gating: a slow branch fetch, diff, or list_changed_files must degrade
the evidence rather than hold the whole flow-verb request (and its
transaction's row locks) hostage for the full ``flow_verb_timeout_seconds``
budget. ``run_bounded_leg`` wraps one such awaitable in ``asyncio.wait_for``;
on a timeout it appends a human-readable entry to the caller's
``evidence_gaps`` list, logs one structured warning, and returns the
caller's ``default`` instead of propagating. Fail-closed paths (``i_am_done``
/ ``pr_pass`` conventions enforcement) do not use this — they keep their
existing hard cap.

Two distinct timeout shapes get caught, because they fire from different
layers:

- ``TimeoutError`` — asyncio's own cancellation-converted timeout, raised by
  ``asyncio.wait_for`` itself when ``coro`` is still running at the deadline
  (e.g. a slow DB read, a lock wait, or a git op that outlives its own
  internal bound).
- ``GitTimeoutError`` — ``GitService._run_git``'s own internal subprocess
  bound (``settings.git_command_timeout_seconds``, 30s by default — usually
  SHORTER than a leg's own budget, making this the most common real-world
  timeout shape for a single hung git call). It is a ``GitError`` /
  ``RobocoError`` subclass, NOT a ``TimeoutError`` subclass, and is raised
  from INSIDE the coroutine (the subprocess itself gave up), not by
  ``wait_for``'s cancellation. Any OTHER ``GitError`` (a real command
  failure — bad ref, auth, network refusal, not a timeout) still propagates
  uncaught; only a timeout degrades.

The conventions-validator subprocess leg does NOT go through
``run_bounded_leg`` — see ``QAMixin._qa_convention_findings``'s docstring
for why nesting an outer wait_for around a coroutine with its own inner
wait_for-based cleanup (``proc.kill()`` / ``proc.wait()``) can orphan the
child process.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

import structlog

from roboco.exceptions import GitTimeoutError

if TYPE_CHECKING:
    from collections.abc import Coroutine

logger = structlog.get_logger()

# Floor so a nearly-exhausted budget still gives the next leg a real chance
# to run (and a real wait_for timeout > 0) instead of skipping it outright.
_MIN_LEG_SECONDS = 1.0


class LegBudget:
    """A shared, once-per-evidence-build deadline for a sequence of legs.

    Per-leg budgets summed naively can exceed the flow-verb wall (e.g. three
    45s legs = 135s, well past the 120s verb timeout). One ``LegBudget``
    instance per evidence build gives each leg only what's left of the
    TOTAL assembly budget — shrinking as legs consume it, never resetting
    per leg — so the whole build's wall time is capped near the configured
    total regardless of how many legs it runs.
    """

    __slots__ = ("_deadline",)

    def __init__(self, total_seconds: float) -> None:
        self._deadline = time.monotonic() + total_seconds

    def remaining(self) -> float:
        """Seconds left before the deadline, floored at ``_MIN_LEG_SECONDS``."""
        return max(_MIN_LEG_SECONDS, self._deadline - time.monotonic())


async def run_bounded_leg[T](
    coro: Coroutine[Any, Any, T],
    *,
    default: T,
    budget: LegBudget,
    leg: str,
    hint: str,
    task_id: Any,
    gaps: list[str],
) -> T:
    """Await ``coro`` bounded by ``budget``'s remaining time.

    Degrades to ``default`` and appends one entry to ``gaps`` on either
    timeout shape (see module docstring); the entry names which bound
    tripped so a reader can tell a slow-but-alive leg (the assembly budget)
    from a git subprocess that gave up on its own shorter bound.

    Cancelling a ``run_in_executor``/``asyncio.to_thread``-backed git call
    abandons the worker thread — this only stops AWAITING it, not the
    underlying subprocess. Each git-touching leg's own subprocess call is
    independently bounded (``git_command_timeout_seconds`` inside
    ``_run_git``, or an explicit ``subprocess_timeout`` on
    ``fetch_branch_for_inspection``) so the thread still self-terminates
    near its own window rather than running to completion unbounded.
    """
    timeout = budget.remaining()
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except TimeoutError:
        gaps.append(
            f"{leg} unavailable: timed out after {timeout:.0f}s "
            f"(evidence-assembly budget) — {hint}"
        )
        logger.warning(
            "evidence_leg_timeout",
            leg=leg,
            timeout=timeout,
            task_id=str(task_id),
            bound="assembly_budget",
        )
        return default
    except GitTimeoutError as exc:
        gaps.append(
            f"{leg} unavailable: a git command timed out after {exc.timeout}s "
            f"(git_command_timeout_seconds) — {hint}"
        )
        logger.warning(
            "evidence_leg_timeout",
            leg=leg,
            timeout=exc.timeout,
            task_id=str(task_id),
            bound="git_command_timeout",
        )
        return default
