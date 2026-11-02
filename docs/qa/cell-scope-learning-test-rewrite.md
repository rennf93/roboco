# CELL-scope learning test rewrite (post-#914)

Task 6d4baecf, PR #1118. Test-only change in `tests/integration/test_learning_service.py`; `roboco/services/learning.py` is untouched.

## Background

PR #914 (commit 04eb61da) made `_fetch_notify_agents` filter CELL-scope learnings to the author's team. The old test, `test_cell_scope_runs_without_role_filter`, predated that filter. It hardcoded both fake agents to `Team.BACKEND`, so its same-team assertion passed without exercising the filter. It also read `svc._notification_queue`, an in-memory internal.

## What changed

- Renamed to `test_cell_scope_notifies_author_team_excludes_cross_cell`, with a docstring describing the team-filtered behavior.
- `_make_agent` gained a `team: Team = Team.BACKEND` parameter (default keeps existing callers unchanged).
- The test seeds an author, a same-team QA peer, and a `Team.FRONTEND` cross-cell developer.
- It asserts on persisted `NotificationTable` rows (`type == KNOWLEDGE_SHARE`, `from_agent == learning.agent_id`): the peer id is in the union of `to_agents`, the cross-cell id is not.
- No in-memory queue state is read, so the test survives the queue lifecycle rework in the sibling leaf.
- A trailing inline comment on the ORG-scope test's `"x" * 250` content was dropped (QA finding F-6136ad56).

## Verification

The file suite (`tests/integration/test_learning_service.py`) passed on sandbox Postgres: 8 passed. `make gate` could not run in the dev container because of a `.venv` permission error; `ruff check` passed when run directly. Generated artifacts from an earlier commit were reverted (QA finding F-920c1c34); the net diff against base is the single test file.

## Not documented

No user-facing or API docs change: no runtime behavior changed, and no existing docs reference the old test name.
