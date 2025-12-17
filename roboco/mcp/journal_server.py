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

import httpx
from fastapi import status
from mcp.server.fastmcp import FastMCP

from roboco.agents_config import get_agent_role
from roboco.config import settings
from roboco.llm import ToonAdapter
from roboco.mcp.schemas import (
    DecisionLogInput,
    JournalEntryInput,
    LearningInput,
    StruggleInput,
    TaskReflectionInput,
)

# Global TOON adapter for encoding journal data
_toon = ToonAdapter()


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def _format_error_response(
    error_code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Format a standardized error response."""
    return {
        "error": {
            "code": error_code,
            "message": message,
            "details": details or {},
        }
    }


def _get_agent_headers(agent_id: str) -> dict[str, str]:
    """Get standard headers for API calls."""
    return {
        "X-Agent-Id": agent_id,
        "X-Agent-Role": get_agent_role(agent_id),
    }


async def _post_journal_entry(
    endpoint: str,
    payload: dict[str, Any],
    agent_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Post to a journal endpoint. Returns (data, error)."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.internal_api_url}/journals/me/{endpoint}",
            json=payload,
            headers=_get_agent_headers(agent_id),
        )
        if resp.status_code not in [200, 201]:
            return None, _format_error_response(
                "CREATE_FAILED",
                f"Failed to create {endpoint.rstrip('s')}",
                {"api_error": resp.text},
            )
        return resp.json(), None


# =============================================================================
# TOOL IMPLEMENTATIONS
# =============================================================================


async def _handle_journal_entry(
    data: JournalEntryInput, agent_id: str
) -> dict[str, Any]:
    """Handle journal entry creation."""
    valid_types = ["general", "task_reflection", "decision_log", "learning", "struggle"]
    if data.entry_type not in valid_types:
        return _format_error_response(
            "INVALID_TYPE",
            f"Invalid entry type. Must be one of: {valid_types}",
        )

    payload = {
        "type": data.entry_type,
        "title": data.title,
        "content": data.content,
        "task_id": data.task_id,
        "tags": data.tags,
        "is_private": data.is_private,
    }

    entry, error = await _post_journal_entry("entries", payload, agent_id)
    if error or entry is None:
        return error or _format_error_response("ERROR", "Failed to create entry")

    return {
        "status": "created",
        "entry": entry,
        "entry_toon": _toon.encode(entry),
        "guidance": (
            "Journal entry saved. Use roboco_journal_search to find past entries."
        ),
    }


async def _handle_reflect(data: TaskReflectionInput, agent_id: str) -> dict[str, Any]:
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

    entry, error = await _post_journal_entry("reflections", payload, agent_id)
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


async def _handle_decision(data: DecisionLogInput, agent_id: str) -> dict[str, Any]:
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

    entry, error = await _post_journal_entry("decisions", payload, agent_id)
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


async def _handle_learning(data: LearningInput, agent_id: str) -> dict[str, Any]:
    """Handle learning entry creation."""
    payload = {
        "title": data.title,
        "what_learned": data.what_learned,
        "how_applied": data.how_applied,
        "source": data.source,
        "task_id": data.task_id,
        "tags": data.tags,
    }

    entry, error = await _post_journal_entry("learnings", payload, agent_id)
    if error:
        return error

    return {
        "status": "created",
        "entry": entry,
        "guidance": "Learning recorded. Use tags to make it searchable later.",
    }


async def _handle_struggle(data: StruggleInput, agent_id: str) -> dict[str, Any]:
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

    entry, error = await _post_journal_entry("struggles", payload, agent_id)
    if error:
        return error

    guidance = "Struggle recorded."
    if data.help_needed and not data.resolution:
        guidance += (
            " Since you indicated help is needed, consider asking in your cell channel."
        )

    return {"status": "created", "entry": entry, "guidance": guidance}


async def _handle_search(query: str, top_k: int, agent_id: str) -> dict[str, Any]:
    """Handle journal search."""
    async with httpx.AsyncClient() as client:
        payload = {"query": query, "top_k": min(top_k, 20)}
        resp = await client.post(
            f"{settings.internal_api_url}/journals/me/search",
            json=payload,
            headers=_get_agent_headers(agent_id),
        )

        if resp.status_code != status.HTTP_200_OK:
            return _format_error_response(
                "SEARCH_FAILED", "Failed to search journal", {"api_error": resp.text}
            )

        entries = resp.json()

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


async def _handle_stats(agent_id: str) -> dict[str, Any]:
    """Handle journal stats retrieval."""
    async with httpx.AsyncClient() as client:
        stats_resp = await client.get(
            f"{settings.internal_api_url}/journals/me/stats",
            headers=_get_agent_headers(agent_id),
        )
        growth_resp = await client.get(
            f"{settings.internal_api_url}/journals/me/growth",
            headers=_get_agent_headers(agent_id),
        )

        stats = (
            stats_resp.json() if stats_resp.status_code == status.HTTP_200_OK else {}
        )
        growth = (
            growth_resp.json() if growth_resp.status_code == status.HTTP_200_OK else {}
        )

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
    agent_id: str,
) -> dict[str, Any]:
    """Handle recent entries retrieval."""
    async with httpx.AsyncClient() as client:
        params: dict[str, Any] = {"limit": min(limit, 50)}
        if entry_type:
            params["entry_type"] = entry_type
        if task_id:
            params["task_id"] = task_id

        resp = await client.get(
            f"{settings.internal_api_url}/journals/me/entries",
            params=params,
            headers=_get_agent_headers(agent_id),
        )

        if resp.status_code != status.HTTP_200_OK:
            return _format_error_response("LIST_FAILED", "Failed to list entries")

        entries = resp.json()

    return {"entries": entries, "count": len(entries)}


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

    @mcp.tool()
    async def roboco_journal_entry(data: JournalEntryInput) -> dict[str, Any]:
        """
        Create a general journal entry.

        Your journal is personal - use it to track thoughts, progress,
        and document your journey on tasks.
        """
        return await _handle_journal_entry(data, agent_id)

    @mcp.tool()
    async def roboco_journal_reflect(data: TaskReflectionInput) -> dict[str, Any]:
        """
        Add a task reflection entry.

        IMPORTANT: Call this when completing a task. Reflections help build
        institutional memory and track your growth.
        """
        return await _handle_reflect(data, agent_id)

    @mcp.tool()
    async def roboco_journal_decision(data: DecisionLogInput) -> dict[str, Any]:
        """
        Log a decision you made.

        Use when choosing between approaches. Creates a record of WHY
        you made the decision for future context.
        """
        return await _handle_decision(data, agent_id)

    @mcp.tool()
    async def roboco_journal_learning(data: LearningInput) -> dict[str, Any]:
        """
        Log something you learned.

        Track learnings to build your knowledge base and help future you.
        """
        return await _handle_learning(data, agent_id)

    @mcp.tool()
    async def roboco_journal_struggle(data: StruggleInput) -> dict[str, Any]:
        """
        Log a struggle or challenge.

        Recording struggles helps track problem-solving patterns and
        create documentation for others.
        """
        return await _handle_struggle(data, agent_id)

    @mcp.tool()
    async def roboco_journal_search(query: str, top_k: int = 5) -> dict[str, Any]:
        """
        Search your past journal entries.

        Uses semantic search to find relevant entries based on meaning.
        """
        return await _handle_search(query, top_k, agent_id)

    @mcp.tool()
    async def roboco_journal_stats() -> dict[str, Any]:
        """
        Get statistics about your journal.

        Returns counts by entry type, growth metrics, and other stats.
        """
        return await _handle_stats(agent_id)

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
        return await _handle_recent(entry_type, task_id, limit, agent_id)

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
