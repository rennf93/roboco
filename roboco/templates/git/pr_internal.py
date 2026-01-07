"""
Internal PR Template Builder.

Generates PR body for internal merges (subtask → parent branch).
Cell PM and Main PM approve these internal PRs.
"""

from dataclasses import dataclass, field

from roboco.templates.git.constants import COMMIT_HASH_SHORT_LENGTH, UUID_SHORT_LENGTH


@dataclass
class InternalCommitInfo:
    """Information about a commit for internal PR."""

    hash: str
    message: str


@dataclass
class InternalPRContext:
    """Context for building internal PR body."""

    # Task info
    task_id: str
    task_title: str
    task_description: str
    task_status: str
    task_assigned_to: str | None

    # Parent task info
    parent_task_id: str | None
    parent_task_title: str | None

    # Branch info
    source_branch: str
    target_branch: str

    # Commits
    commits: list[InternalCommitInfo] = field(default_factory=list)

    # Session
    session_id: str | None = None

    # QA info (if passed QA)
    qa_notes: str | None = None
    qa_passed: bool = False


def _format_commits_list(commits: list[InternalCommitInfo]) -> str:
    """Format commits as a simple list."""
    if not commits:
        return "_No commits_"

    lines = []
    for c in commits:
        short_hash = c.hash[:COMMIT_HASH_SHORT_LENGTH]
        lines.append(f"- `{short_hash}` {c.message}")

    return "\n".join(lines)


def _format_qa_section(qa_notes: str | None, qa_passed: bool) -> str:
    """Format QA notes section."""
    if not qa_passed:
        return "_Pending QA review_"

    if qa_notes:
        return f"**QA Passed**\n\n{qa_notes}"

    return "**QA Passed** (no notes)"


def build_pr_body_internal(ctx: InternalPRContext, api_base: str) -> str:
    """Build PR body for internal merge (subtask → parent).

    Args:
        ctx: InternalPRContext with task and branch information
        api_base: API base URL for links

    Returns:
        Formatted PR body markdown
    """
    api_base = api_base.rstrip("/")

    commits_section = _format_commits_list(ctx.commits)
    qa_section = _format_qa_section(ctx.qa_notes, ctx.qa_passed)

    # Build links section
    links_lines = [f"- **Task**: {api_base}/tasks/{ctx.task_id}"]
    if ctx.parent_task_id:
        links_lines.append(f"- **Parent**: {api_base}/tasks/{ctx.parent_task_id}")
    if ctx.session_id:
        links_lines.append(f"- **Session**: {api_base}/sessions/{ctx.session_id}")

    links_section = "\n".join(links_lines)

    # Parent info for context
    parent_info = ""
    if ctx.parent_task_id and ctx.parent_task_title:
        parent_info = (
            f"\n**Parent Task**: {ctx.parent_task_title} (`{ctx.parent_task_id}`)\n"
        )

    return f"""## Merge: `{ctx.source_branch}` → `{ctx.target_branch}`

**Task**: {ctx.task_title} (`{ctx.task_id}`)
**Agent**: {ctx.task_assigned_to or "Unassigned"}
**Status**: {ctx.task_status}
{parent_info}
## Summary

{ctx.task_description}

## Commits ({len(ctx.commits)})

{commits_section}

## Links

{links_section}

## QA Notes

{qa_section}
"""


def build_pr_title_internal(ctx: InternalPRContext) -> str:
    """Build PR title for internal merge.

    Args:
        ctx: InternalPRContext with task info

    Returns:
        PR title string
    """
    task_short = ctx.task_id[:UUID_SHORT_LENGTH]
    return f"[{task_short}] {ctx.task_title}"
