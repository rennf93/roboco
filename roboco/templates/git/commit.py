"""
Commit Message Builder.

Generates rich commit messages with full traceability links.

Format:
    [{root_id}:{task_id}] {type}({scope}): {description}

    {body}

    ---
    Task: {task_id}
    Root: {root_task_id}
    Agent: {agent_slug}
    Session: {session_id}

    Links:
    - Task: {api_base}/tasks/{task_id}
    - Root: {api_base}/tasks/{root_task_id}
    - Journal: {api_base}/journals/{agent_slug}
    - Session: {api_base}/sessions/{session_id}
"""

from dataclasses import dataclass

from roboco.templates.git.constants import COMMIT_TYPES, UUID_SHORT_LENGTH


class CommitMessageError(Exception):
    """Error building commit message."""


@dataclass
class CommitContext:
    """Context for building a commit message."""

    task_id: str
    root_task_id: str
    agent_slug: str
    session_id: str | None
    commit_type: str
    scope: str | None
    description: str
    body: str | None = None

    def __post_init__(self) -> None:
        """Validate commit context."""
        if self.commit_type not in COMMIT_TYPES:
            raise CommitMessageError(
                f"Invalid commit type '{self.commit_type}'. "
                f"Must be one of: {', '.join(sorted(COMMIT_TYPES))}"
            )
        if not self.description:
            raise CommitMessageError("Commit description is required")
        if not self.task_id:
            raise CommitMessageError("Task ID is required")
        if not self.root_task_id:
            raise CommitMessageError("Root task ID is required")
        if not self.agent_slug:
            raise CommitMessageError("Agent slug is required")


def build_commit_message(ctx: CommitContext, api_base: str) -> str:
    """Build rich commit message with all traceability links.

    Args:
        ctx: CommitContext with task, agent, and commit details
        api_base: API base URL for building links (e.g., http://localhost:8000/api/v1)

    Returns:
        Formatted commit message with header, body, metadata, and links
    """
    # Build header line
    # Format: [{root_id}:{task_id}] {type}({scope}): {description}
    short_len = UUID_SHORT_LENGTH
    root_short = ctx.root_task_id[:short_len]
    task_short = ctx.task_id[:short_len]

    type_scope = f"{ctx.commit_type}({ctx.scope})" if ctx.scope else ctx.commit_type
    header = f"[{root_short}:{task_short}] {type_scope}: {ctx.description}"

    # Build body section
    body_section = ""
    if ctx.body:
        body_section = f"\n{ctx.body}\n"

    # Build metadata section
    metadata_lines = [
        "---",
        f"Task: {ctx.task_id}",
        f"Root: {ctx.root_task_id}",
        f"Agent: {ctx.agent_slug}",
    ]
    if ctx.session_id:
        metadata_lines.append(f"Session: {ctx.session_id}")

    metadata_section = "\n".join(metadata_lines)

    # Build links section
    api_base = api_base.rstrip("/")
    links_lines = [
        "",
        "Links:",
        f"- Task: {api_base}/tasks/{ctx.task_id}",
        f"- Root: {api_base}/tasks/{ctx.root_task_id}",
        f"- Journal: {api_base}/journals/{ctx.agent_slug}",
    ]
    if ctx.session_id:
        links_lines.append(f"- Session: {api_base}/sessions/{ctx.session_id}")

    links_section = "\n".join(links_lines)

    # Combine all sections
    return f"{header}{body_section}\n{metadata_section}{links_section}"
