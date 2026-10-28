"""A failed commit tells the agent what actually went wrong.

Live 2026-10-28, 8 of 11 failed developer commits were "nothing to commit,
working tree clean", each answered "inspect the git error above and retry",
and the agents retried the same empty commit.
"""

from __future__ import annotations

from roboco.exceptions import GitError
from roboco.services.gateway.content_actions import ContentActions

_CLEAN_TREE = (
    "Command failed: commit -m [dfeb2d8a] feat(panel): add Stuck filter - On "
    "branch feature/frontend/x Your branch is up to date with 'origin/x'. "
    "nothing to commit, working tree clean"
)


def test_an_empty_commit_names_both_real_causes() -> None:
    env = ContentActions._commit_git_error_envelope(GitError(_CLEAN_TREE), files=None)

    assert env.error == "invalid_state"
    remediate = env.remediate or ""
    assert "no changes to commit" in remediate
    assert "already committed" in remediate
    assert "outside this task's workspace" in remediate


def test_a_files_list_matching_nothing_still_names_the_files() -> None:
    env = ContentActions._commit_git_error_envelope(
        GitError(_CLEAN_TREE), files=["agents/prompts/_generated"]
    )

    assert "['agents/prompts/_generated'] matched no modified paths" in (
        env.remediate or ""
    )


def test_any_other_git_failure_keeps_the_generic_advice() -> None:
    env = ContentActions._commit_git_error_envelope(
        GitError("fatal: unable to write new index file"), files=None
    )

    assert env.remediate == "inspect the git error above and retry"
