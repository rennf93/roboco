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
