"""
Root Task PR Template Builder.

Generates PR body for root tasks (CEO level) with full task tree links.
Only root tasks create PRs to main - CEO approves these.
"""

from dataclasses import dataclass, field

from roboco.templates.git.constants import COMMIT_HASH_SHORT_LENGTH


@dataclass
class SubtaskInfo:
    """Information about a subtask for PR template."""

    id: str
    title: str
    status: str
    assigned_to: str | None
    branch_name: str | None
    commit_count: int = 0


@dataclass
class CommitInfo:
    """Information about a commit for PR template."""

    hash: str
    message: str
    agent_slug: str


@dataclass
class RootPRContext:
    """Context for building root task PR body."""

    # Root task info
    root_task_id: str
    root_task_title: str
    root_task_description: str
    root_task_assigned_to: str | None
    root_task_type: str  # feature, bug, chore, docs, hotfix

    # Tree info
    subtasks: list[SubtaskInfo] = field(default_factory=list)
    commits: list[CommitInfo] = field(default_factory=list)

    # Session info
    primary_session_id: str | None = None
    additional_sessions: list[str] = field(default_factory=list)

    # Agent info (agents who worked on tree)
    agent_slugs: list[str] = field(default_factory=list)

    # Acceptance criteria (for testing checklist)
    acceptance_criteria: list[str] = field(default_factory=list)


def _get_change_type_checkboxes(task_type: str) -> str:
    """Generate type of change checkboxes with appropriate one checked."""
    type_map = {
        "bug": "Bug fix",
        "hotfix": "Bug fix",
        "feature": "New feature",
        "docs": "Documentation",
        "chore": "Code cleanup or refactoring",
    }

    types = [
        "Bug fix",
        "New feature",
        "Breaking change",
        "Documentation",
        "Performance improvement",
        "Code cleanup or refactoring",
    ]

    checked_type = type_map.get(task_type, "")
    lines = []
    for t in types:
        checkbox = "[x]" if t == checked_type else "[ ]"
        lines.append(f"- {checkbox} {t}")

    return "\n".join(lines)


def _format_subtasks_section(subtasks: list[SubtaskInfo], api_base: str) -> str:
    """Format the subtasks section of the PR."""
    if not subtasks:
        return "_No subtasks_"

    lines = []
    for st in subtasks:
        status_badge = f"[{st.status}]"
        assigned = st.assigned_to or "Unassigned"
        branch = st.branch_name or "No branch"

        lines.append(f"- {status_badge} **{st.title}** (`{st.id}`)")
        lines.append(f"  - Agent: {assigned}")
        lines.append(f"  - Branch: `{branch}`")
        lines.append(f"  - Commits: {st.commit_count}")
        lines.append(f"  - Link: {api_base}/tasks/{st.id}")

    return "\n".join(lines)


def _format_commits_by_agent(commits: list[CommitInfo]) -> str:
    """Group and format commits by agent."""
    if not commits:
        return "_No commits_"

    # Group by agent
    by_agent: dict[str, list[CommitInfo]] = {}
    for c in commits:
        by_agent.setdefault(c.agent_slug, []).append(c)

    lines = []
    for agent, agent_commits in sorted(by_agent.items()):
        lines.append(f"\n### {agent}")
        for c in agent_commits:
            short_hash = c.hash[:COMMIT_HASH_SHORT_LENGTH]
            lines.append(f"- `{short_hash}` {c.message}")

    return "\n".join(lines)


def _format_sessions(
    primary_session_id: str | None,
    additional_sessions: list[str],
    api_base: str,
) -> str:
    """Format sessions section."""
    lines = []
    if primary_session_id:
        lines.append(f"- **Primary**: {api_base}/sessions/{primary_session_id}")
    for sid in additional_sessions:
        lines.append(f"- {api_base}/sessions/{sid}")

    return "\n".join(lines) if lines else "_No sessions linked_"


def _format_journals(agent_slugs: list[str], root_task_id: str, api_base: str) -> str:
    """Format journal entries section."""
    if not agent_slugs:
        return "_No journal entries_"

    lines = []
    for slug in sorted(set(agent_slugs)):
        lines.append(f"- **{slug}**: {api_base}/journals/{slug}?task={root_task_id}")

    return "\n".join(lines)


def _format_testing(acceptance_criteria: list[str]) -> str:
    """Format testing section from acceptance criteria."""
    if not acceptance_criteria:
        return "_No acceptance criteria defined_"

    lines = []
    for criterion in acceptance_criteria:
        lines.append(f"- [ ] {criterion}")

    return "\n".join(lines)


def build_pr_body_root(ctx: RootPRContext, api_base: str) -> str:
    """Build PR body for root task (CEO level).

    Args:
        ctx: RootPRContext with all task tree information
        api_base: API base URL for links (e.g., http://localhost:8000/api/v1)

    Returns:
        Formatted PR body markdown
    """
    api_base = api_base.rstrip("/")

    # Build all sections
    change_type = _get_change_type_checkboxes(ctx.root_task_type)
    subtasks_section = _format_subtasks_section(ctx.subtasks, api_base)
    commits_section = _format_commits_by_agent(ctx.commits)
    sessions_section = _format_sessions(
        ctx.primary_session_id, ctx.additional_sessions, api_base
    )
    journals_section = _format_journals(ctx.agent_slugs, ctx.root_task_id, api_base)
    testing_section = _format_testing(ctx.acceptance_criteria)

    return f"""## Summary

**{ctx.root_task_title}**

{ctx.root_task_description}

## Type of Change

{change_type}

## Task Hierarchy

### Root Task
- **ID**: `{ctx.root_task_id}`
- **Title**: {ctx.root_task_title}
- **Assigned**: {ctx.root_task_assigned_to or "Unassigned"}
- **Link**: {api_base}/tasks/{ctx.root_task_id}

### Subtasks ({len(ctx.subtasks)})

{subtasks_section}

## Commits by Agent
{commits_section}

## Sessions

{sessions_section}

## Journal Entries

{journals_section}

## Testing

{testing_section}

## Checklist

- [ ] Code follows project style (Mypy, Ruff, Vulture, Bandit)
- [ ] Tests added/updated
- [ ] All tests pass
- [ ] Documentation updated
"""


def build_pr_title_root(ctx: RootPRContext) -> str:
    """Build PR title for root task.

    Args:
        ctx: RootPRContext with root task info

    Returns:
        PR title string
    """
    type_prefix = ctx.root_task_type.capitalize()
    return f"[{type_prefix}] {ctx.root_task_title}"
