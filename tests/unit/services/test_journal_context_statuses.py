"""Regression: PENDING tasks are valid journal-context targets.

The decisions-audit board program works a PENDING task without claiming
it; the auditor's note() must auto-attach that task id or the
complete-at-note seam never fires and the program wedges forever
(a9739b1c, 2026-09-29).
"""

from roboco.models.base import TaskStatus
from roboco.services.task import TaskService


def test_journal_context_statuses_include_pending() -> None:
    assert TaskStatus.PENDING in TaskService._JOURNAL_CONTEXT_STATUSES
