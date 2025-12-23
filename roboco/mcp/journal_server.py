"""
Journal MCP Server

Exposes journal tools to Claude Code agents for personal reflection,
learning tracking, and context persistence.

Tools:
- roboco_journal_entry: Create a journal entry
- roboco_journal_reflect: Add task reflection (when completing task)
- roboco_journal_decision: Log a decision
- roboco_journal_learning: Log something learned
- roboco_journal_struggle: Log a struggle
- roboco_journal_search: Search past entries
- roboco_journal_stats: Get journal statistics
"""

from typing import Any

from mcp.server.fastmcp import FastMCP

from roboco.agents_config import get_agent_cell, get_agent_role
from roboco.enforcement.journal_perms import can_read_journal, get_readable_journals
from roboco.llm import ToonAdapter
from roboco.mcp.schemas import (
    DecisionLogInput,
    JournalEntryInput,
    LearningInput,
    StruggleInput,
    TaskReflectionInput,
)
from roboco.mcp.utils import ApiClient, format_error_response

# Global TOON adapter for encoding journal data
_toon = ToonAdapter()

# Valid entry types
VALID_ENTRY_TYPES = frozenset(
    ["general", "task_reflection", "decision_log", "learning", "struggle"]
)


# =============================================================================
# TOOL IMPLEMENTATIONS
# =============================================================================


async def _handle_journal_entry(
    data: JournalEntryInput, client: ApiClient
) -> dict[str, Any]:
    """Handle journal entry creation."""
    if data.entry_type not in VALID_ENTRY_TYPES:
        return format_error_response(
            "INVALID_TYPE",
            f"Invalid entry type. Must be one of: {list(VALID_ENTRY_TYPES)}",
        )

    payload = {
        "type": data.entry_type,
        "title": data.title,
        "content": data.content,
        "task_id": data.task_id,
        "session_id": data.session_id,
        "tags": data.tags,
        "is_private": data.is_private,
    }

    entry, error = await client.post_or_error(
        "/journals/me/entries",
        json=payload,
        error_code="CREATE_FAILED",
        error_message="Failed to create entry",
    )
    if error or entry is None:
        return error or format_error_response("ERROR", "Failed to create entry")

    return {
        "status": "created",
        "entry": entry,
        "entry_toon": _toon.encode(entry),
        "guidance": (
            "Journal entry saved. Use roboco_journal_search to find past entries."
        ),
    }


async def _handle_reflect(
    data: TaskReflectionInput, client: ApiClient
) -> dict[str, Any]:
    """Handle task reflection creation."""
    payload = {
        "task_id": data.task_id,
        "session_id": data.session_id,
        "title": data.title,
        "what_done": data.what_done,
        "what_learned": data.what_learned,
        "what_struggled": data.what_struggled,
        "next_steps": data.next_steps,
        "tags": data.tags,
    }

    entry, error = await client.post_or_error(
        "/journals/me/reflections",
        json=payload,
        error_code="CREATE_FAILED",
        error_message="Failed to create reflection",
    )
    if error:
        return error

    return {
        "status": "created",
        "entry": entry,
        "guidance": (
            "Reflection saved. This will help you (and future you) "
            "when working on similar tasks."
        ),
    }


async def _handle_decision(data: DecisionLogInput, client: ApiClient) -> dict[str, Any]:
    """Handle decision log creation."""
    payload = {
        "title": data.title,
        "context": data.context,
        "options": [opt.model_dump() for opt in data.options],
        "chosen": data.chosen,
        "rationale": data.rationale,
        "consequences": data.consequences,
        "task_id": data.task_id,
        "tags": data.tags,
    }

    entry, error = await client.post_or_error(
        "/journals/me/decisions",
        json=payload,
        error_code="CREATE_FAILED",
        error_message="Failed to create decision log",
    )
    if error:
        return error

    return {
        "status": "created",
        "entry": entry,
        "guidance": (
            "Decision logged. If you need to revisit this decision later, "
            "you'll have the context of why it was made."
        ),
    }


async def _handle_learning(data: LearningInput, client: ApiClient) -> dict[str, Any]:
    """Handle learning entry creation."""
    payload = {
        "title": data.title,
        "what_learned": data.what_learned,
        "how_applied": data.how_applied,
        "source": data.source,
        "task_id": data.task_id,
        "tags": data.tags,
    }

    entry, error = await client.post_or_error(
        "/journals/me/learnings",
        json=payload,
        error_code="CREATE_FAILED",
        error_message="Failed to create learning entry",
    )
    if error:
        return error

    return {
        "status": "created",
        "entry": entry,
        "guidance": "Learning recorded. Use tags to make it searchable later.",
    }


async def _handle_struggle(data: StruggleInput, client: ApiClient) -> dict[str, Any]:
    """Handle struggle entry creation."""
    payload = {
        "title": data.title,
        "what_struggled": data.what_struggled,
        "attempted_solutions": data.attempted_solutions,
        "resolution": data.resolution,
        "help_needed": data.help_needed,
        "task_id": data.task_id,
        "tags": data.tags,
    }

    entry, error = await client.post_or_error(
        "/journals/me/struggles",
        json=payload,
        error_code="CREATE_FAILED",
        error_message="Failed to create struggle entry",
    )
    if error:
        return error

    guidance = "Struggle recorded."
    if data.help_needed and not data.resolution:
        guidance += (
            " Since you indicated help is needed, consider asking in your cell channel."
        )

    return {"status": "created", "entry": entry, "guidance": guidance}


async def _handle_search(query: str, top_k: int, client: ApiClient) -> dict[str, Any]:
    """Handle journal search."""
    max_results = 20
    payload = {"query": query, "top_k": min(top_k, max_results)}

    entries, error = await client.post_or_error(
        "/journals/me/search",
        json=payload,
        error_code="SEARCH_FAILED",
        error_message="Failed to search journal",
    )
    if error:
        return error

    if not entries:
        return {
            "entries": [],
            "guidance": "No matching entries found. Try different keywords.",
        }

    return {
        "entries": entries,
        "count": len(entries),
        "guidance": f"Found {len(entries)} relevant entries.",
    }


async def _handle_stats(client: ApiClient) -> dict[str, Any]:
    """Handle journal stats retrieval."""
    # Fetch stats and growth in parallel would be better but keep simple for now
    stats_resp = await client.get("/journals/me/stats")
    growth_resp = await client.get("/journals/me/growth")

    stats = stats_resp.json() if stats_resp.ok else {}
    growth = growth_resp.json() if growth_resp.ok else {}

    return {
        "total_entries": stats.get("total_entries", 0),
        "entries_by_type": stats.get("entries_by_type", {}),
        "last_entry_at": stats.get("last_entry_at"),
        "growth_metrics": {
            "total_reflections": growth.get("total_reflections", 0),
            "total_learnings": growth.get("total_learnings", 0),
            "total_struggles": growth.get("total_struggles", 0),
            "total_decisions": growth.get("total_decisions", 0),
            "struggle_resolution_rate": growth.get("struggle_resolution_rate", 0),
            "sentiment_trend": growth.get("sentiment_trend", "stable"),
        },
        "guidance": (
            "These stats reflect your journal activity. "
            "Regular journaling helps build context for future sessions."
        ),
    }


async def _handle_recent(
    entry_type: str | None,
    task_id: str | None,
    limit: int,
    client: ApiClient,
) -> dict[str, Any]:
    """Handle recent entries retrieval."""
    max_limit = 50
    params: dict[str, Any] = {"limit": min(limit, max_limit)}
    if entry_type:
        params["entry_type"] = entry_type
    if task_id:
        params["task_id"] = task_id

    entries, error = await client.get_or_error(
        "/journals/me/entries",
        params=params,
        error_code="LIST_FAILED",
        error_message="Failed to list entries",
    )
    if error:
        return error

    return {"entries": entries, "count": len(entries) if entries else 0}


async def _handle_team_entries(
    target_agent: str,
    reader_agent: str,
    params: dict[str, Any],
    client: ApiClient,
) -> dict[str, Any]:
    """Handle reading another agent's journal entries."""
    # Check permission
    can_read, reason = can_read_journal(reader_agent, target_agent)
    if not can_read:
        return format_error_response("ACCESS_DENIED", reason)

    entries, error = await client.get_or_error(
        f"/journals/{target_agent}/entries",
        params=params,
        error_code="READ_FAILED",
        error_message=f"Failed to read {target_agent}'s journal",
    )
    if error:
        return error

    return {
        "agent": target_agent,
        "entries": entries,
        "count": len(entries) if entries else 0,
        "guidance": f"Showing entries from {target_agent}'s journal.",
    }


def _get_journal_scope(agent_id: str) -> dict[str, Any]:
    """Get information about what journals an agent can read."""
    scope_info = get_readable_journals(agent_id)
    role = get_agent_role(agent_id)
    cell = get_agent_cell(agent_id)

    return {
        "your_agent": agent_id,
        "your_role": role,
        "your_cell": cell,
        "scope": scope_info,
        "guidance": scope_info.get("description", ""),
    }


# =============================================================================
# MCP SERVER FACTORY
# =============================================================================


def create_journal_mcp_server(agent_id: str) -> FastMCP:
    """
    Create a Journal MCP server for a specific agent.

    Args:
        agent_id: The agent identifier (e.g., "be-dev-1")

    Returns:
        Configured FastMCP server
    """
    mcp = FastMCP(f"roboco-journal-{agent_id}", json_response=True)

    # Create shared API client for this agent
    client = ApiClient(agent_id)

    @mcp.tool()
    async def roboco_journal_entry(data: JournalEntryInput) -> dict[str, Any]:
        """
        Create a general journal entry.

        Your journal is personal - use it to track thoughts, progress,
        and document your journey on tasks.
        """
        return await _handle_journal_entry(data, client)

    @mcp.tool()
    async def roboco_journal_reflect(data: TaskReflectionInput) -> dict[str, Any]:
        """
        Add a task reflection entry.

        IMPORTANT: Call this when completing a task. Reflections help build
        institutional memory and track your growth.
        """
        return await _handle_reflect(data, client)

    @mcp.tool()
    async def roboco_journal_decision(data: DecisionLogInput) -> dict[str, Any]:
        """
        Log a decision you made.

        Use when choosing between approaches. Creates a record of WHY
        you made the decision for future context.
        """
        return await _handle_decision(data, client)

    @mcp.tool()
    async def roboco_journal_learning(data: LearningInput) -> dict[str, Any]:
        """
        Log something you learned.

        Track learnings to build your knowledge base and help future you.
        """
        return await _handle_learning(data, client)

    @mcp.tool()
    async def roboco_journal_struggle(data: StruggleInput) -> dict[str, Any]:
        """
        Log a struggle or challenge.

        Recording struggles helps track problem-solving patterns and
        create documentation for others.
        """
        return await _handle_struggle(data, client)

    @mcp.tool()
    async def roboco_journal_search(query: str, top_k: int = 5) -> dict[str, Any]:
        """
        Search your past journal entries.

        Uses semantic search to find relevant entries based on meaning.
        """
        return await _handle_search(query, top_k, client)

    @mcp.tool()
    async def roboco_journal_stats() -> dict[str, Any]:
        """
        Get statistics about your journal.

        Returns counts by entry type, growth metrics, and other stats.
        """
        return await _handle_stats(client)

    @mcp.tool()
    async def roboco_journal_recent(
        entry_type: str | None = None,
        task_id: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """
        List recent journal entries.

        Filter by entry_type (general, task_reflection, decision_log,
        learning, struggle) or by task_id.
        """
        return await _handle_recent(entry_type, task_id, limit, client)

    # =========================================================================
    # TEAM JOURNAL ACCESS TOOLS
    # =========================================================================

    @mcp.tool()
    async def roboco_journal_read_team(
        target_agent: str,
        entry_type: str | None = None,
        task_id: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """
        Read journal entries from a teammate.

        Cell members can read each other's journals (including private entries).
        PMs can read across cells. Main PM, Board, and Auditor have broader access.

        Args:
            target_agent: Agent slug to read from (e.g., "be-dev-1", "be-qa")
            entry_type: Optional filter by type
            task_id: Optional filter by task
            limit: Max entries to return (default 10)
        """
        max_limit = 50
        params: dict[str, Any] = {"limit": min(limit, max_limit)}
        if entry_type:
            params["entry_type"] = entry_type
        if task_id:
            params["task_id"] = task_id

        return await _handle_team_entries(target_agent, agent_id, params, client)

    @mcp.tool()
    def roboco_journal_scope() -> dict[str, Any]:
        """
        Get information about which journals you can access.

        Shows your role, cell, and what other agents' journals you can read.
        """
        return _get_journal_scope(agent_id)

    return mcp


# =============================================================================
# STANDALONE RUNNER
# =============================================================================

if __name__ == "__main__":
    import sys

    MIN_ARGS = 2
    if len(sys.argv) < MIN_ARGS:
        print("Usage: python journal_server.py <agent_id>")
        sys.exit(1)

    agent_id_arg = sys.argv[1]
    server = create_journal_mcp_server(agent_id_arg)
    server.run()
