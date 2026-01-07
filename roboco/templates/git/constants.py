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

# Maximum task hierarchy depth (root → subtask → sub-subtask)
MAX_TASK_DEPTH: Final[int] = 3

# Git branch name character limit
GIT_BRANCH_MAX_LENGTH: Final[int] = 255

# Short ID lengths for display
UUID_SHORT_LENGTH: Final[int] = 8
COMMIT_HASH_SHORT_LENGTH: Final[int] = 7
