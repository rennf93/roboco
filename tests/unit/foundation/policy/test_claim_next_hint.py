"""The developer claim hint names the task's own worktree.

Sessions start in the clone root, before the claim creates the worktree, and
the old hint ("edit + commit") never said where: fe-dev-1 edited the clone
root's README for 79dbdad0 and its commit came up empty (2026-11-03). Those
stray edits also left the clone root dirty, so its fast-forward never ran.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from roboco.foundation.policy import lifecycle


def test_the_claim_hint_names_the_tasks_worktree() -> None:
    task = SimpleNamespace(id=UUID("79dbdad0-f407-4b7d-a594-269551d2d85e"))

    hint = lifecycle._INTENT_VERBS["i_will_work_on"].next_hint(task)

    assert ".worktrees/79dbdad0/" in hint
    assert "clone root" in hint
    assert "commit(message)" in hint
