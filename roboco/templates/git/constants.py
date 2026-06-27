"""
Git Template Constants.

Branch types, commit types, and URL patterns for git workflow templates.
"""

from typing import Final

# Valid branch type prefixes
BRANCH_TYPES: Final[frozenset[str]] = frozenset(
    {
        "feature",
        "bug",
        "chore",
        "docs",
        "hotfix",
    }
)

# Valid conventional commit types
COMMIT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "feat",
        "fix",
        "chore",
        "docs",
        "refactor",
        "test",
        "style",
        "perf",
        "ci",
        "build",
    }
)

# Maximum task hierarchy depth. MegaTask adds one Main-PM layer on top of the
# normal flow, so the full hierarchy is 4 layers: umbrella (Main PM, depth 0)
# → root-subtask (Main PM, depth 1) → cell task (cell PM, depth 2) → dev
# subtask (depth 3). This was sized at 3 for the pre-MegaTask 3-layer flow
# (root→cell→dev = depths 0,1,2) and never raised when MegaTask shipped, so
# cell-PM delegation of dev subtasks was rejected with MAX_TASK_DEPTH=3 and
# deadlocked the cell PM into a respawn loop (2026-06-27 live meltdown).
# The validator rejects a child whose depth would reach MAX_TASK_DEPTH, so 4
# permits the dev subtask at depth 3 while still capping a 5th layer.
MAX_TASK_DEPTH: Final[int] = 4

# Git branch name character limit
GIT_BRANCH_MAX_LENGTH: Final[int] = 255

# Short ID lengths for display
UUID_SHORT_LENGTH: Final[int] = 8
COMMIT_HASH_SHORT_LENGTH: Final[int] = 7
