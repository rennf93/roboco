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

from roboco.config import settings
from roboco.llm import ToonAdapter

# Global TOON adapter for encoding journal data
_toon = ToonAdapter()

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def _get_api_url() -> str:
    """Get the RoboCo API base URL."""
    return f"http://{settings.host}:{settings.port}/api/v1"


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

    # Store agent context
    mcp.agent_id = agent_id

    # =========================================================================
    # GENERAL ENTRY
    # =========================================================================

    @mcp.tool()
    async def roboco_journal_entry(
        title: str,
        content: str,
        entry_type: str = "general",
        task_id: str | None = None,
        tags: list[str] | None = None,
        is_private: bool = False,
    ) -> dict[str, Any]:
        """
        Create a general journal entry.

        Your journal is personal - use it to:
        - Track your thoughts and progress
        - Record context for future sessions
        - Document your journey on tasks
        - Note things you've learned or struggled with

        Args:
            title: Entry title (short description)
            content: Entry content (detailed text)
            entry_type: Type of entry (general, task_reflection, decision_log, learning, struggle)
            task_id: Optional related task
            tags: Optional list of tags
            is_private: If true, only you and CEO/Auditor can see

        Returns:
            Created entry
        """
        valid_types = [
            "general",
            "task_reflection",
            "decision_log",
            "learning",
            "struggle",
        ]
        if entry_type not in valid_types:
            return _format_error_response(
                "INVALID_TYPE",
                f"Invalid entry type. Must be one of: {valid_types}",
            )

        async with httpx.AsyncClient() as client:
            payload = {
                "type": entry_type,
                "title": title,
                "content": content,
                "task_id": task_id,
                "tags": tags or [],
                "is_private": is_private,
            }

            resp = await client.post(
                f"{_get_api_url()}/journals/me/entries",
                json=payload,
                headers={"X-Agent-Id": agent_id},
            )

            if resp.status_code not in [200, 201]:
                return _format_error_response(
                    "CREATE_FAILED",
                    "Failed to create journal entry",
                    {"api_error": resp.text},
                )

            entry = resp.json()

        return {
            "status": "created",
            "entry": entry,
            "entry_toon": _toon.encode(entry),  # TOON-encoded for LLM token efficiency
            "guidance": "Journal entry saved. Use roboco_journal_search to find past entries.",
        }

    # =========================================================================
    # TASK REFLECTION (Important - called at task completion)
    # =========================================================================

    @mcp.tool()
    async def roboco_journal_reflect(
        task_id: str,
        title: str,
        what_done: str,
        what_learned: str,
        what_struggled: str,
        next_steps: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Add a task reflection entry.

        IMPORTANT: Call this when completing a task. Reflections help you:
        - Build institutional memory
        - Track your growth
        - Provide context for future similar tasks

        Args:
            task_id: The task UUID you're reflecting on
            title: Reflection title
            what_done: What was accomplished
            what_learned: Key learnings from this task
            what_struggled: What was difficult or challenging
            next_steps: Optional list of follow-up items
            tags: Optional list of tags

        Returns:
            Created reflection entry
        """
        async with httpx.AsyncClient() as client:
            payload = {
                "task_id": task_id,
                "title": title,
                "what_done": what_done,
                "what_learned": what_learned,
                "what_struggled": what_struggled,
                "next_steps": next_steps or [],
                "tags": tags or [],
            }

            resp = await client.post(
                f"{_get_api_url()}/journals/me/reflections",
                json=payload,
                headers={"X-Agent-Id": agent_id},
            )

            if resp.status_code not in [200, 201]:
                return _format_error_response(
                    "CREATE_FAILED",
                    "Failed to create reflection",
                    {"api_error": resp.text},
                )

            entry = resp.json()

        return {
            "status": "created",
            "entry": entry,
            "guidance": (
                "Reflection saved. This will help you (and future you) "
                "when working on similar tasks."
            ),
        }

    # =========================================================================
    # DECISION LOG
    # =========================================================================

    @mcp.tool()
    async def roboco_journal_decision(
        title: str,
        context: str,
        options: list[dict[str, str]],
        chosen: str,
        rationale: str,
        consequences: list[str] | None = None,
        task_id: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Log a decision you made.

        Use this when you:
        - Choose between multiple approaches
        - Make architectural decisions
        - Pick one solution over another

        This creates a record of WHY you made the decision,
        which is valuable for future context.

        Args:
            title: Decision title
            context: What situation led to this decision
            options: List of options considered, each with 'option' and 'pros_cons' keys
            chosen: Which option was chosen
            rationale: Why this option was chosen
            consequences: Expected consequences of this decision
            task_id: Optional related task
            tags: Optional list of tags

        Returns:
            Created decision log entry
        """
        two = 2
        if len(options) < two:
            return _format_error_response(
                "INVALID_OPTIONS",
                "Decision log requires at least 2 options",
            )

        async with httpx.AsyncClient() as client:
            payload = {
                "title": title,
                "context": context,
                "options": options,
                "chosen": chosen,
                "rationale": rationale,
                "consequences": consequences or [],
                "task_id": task_id,
                "tags": tags or [],
            }

            resp = await client.post(
                f"{_get_api_url()}/journals/me/decisions",
                json=payload,
                headers={"X-Agent-Id": agent_id},
            )

            if resp.status_code not in [200, 201]:
                return _format_error_response(
                    "CREATE_FAILED",
                    "Failed to create decision log",
                    {"api_error": resp.text},
                )

            entry = resp.json()

        return {
            "status": "created",
            "entry": entry,
            "guidance": (
                "Decision logged. If you need to revisit this decision later, "
                "you'll have the context of why it was made."
            ),
        }

    # =========================================================================
    # LEARNING
    # =========================================================================

    @mcp.tool()
    async def roboco_journal_learning(
        title: str,
        what_learned: str,
        how_applied: str | None = None,
        source: str | None = None,
        task_id: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Log something you learned.

        Track learnings to:
        - Build your knowledge base
        - Help future you with similar problems
        - Share knowledge with the team (if not private)

        Args:
            title: Learning title
            what_learned: The actual learning/insight
            how_applied: How you applied or plan to apply this
            source: Where you learned this (docs, experiment, colleague, etc.)
            task_id: Optional related task
            tags: Optional list of tags

        Returns:
            Created learning entry
        """
        async with httpx.AsyncClient() as client:
            payload = {
                "title": title,
                "what_learned": what_learned,
                "how_applied": how_applied,
                "source": source,
                "task_id": task_id,
                "tags": tags or [],
            }

            resp = await client.post(
                f"{_get_api_url()}/journals/me/learnings",
                json=payload,
                headers={"X-Agent-Id": agent_id},
            )

            if resp.status_code not in [200, 201]:
                return _format_error_response(
                    "CREATE_FAILED",
                    "Failed to create learning entry",
                    {"api_error": resp.text},
                )

            entry = resp.json()

        return {
            "status": "created",
            "entry": entry,
            "guidance": "Learning recorded. Use tags to make it searchable later.",
        }

    # =========================================================================
    # STRUGGLE
    # =========================================================================

    @mcp.tool()
    async def roboco_journal_struggle(
        title: str,
        what_struggled: str,
        attempted_solutions: list[str] | None = None,
        resolution: str | None = None,
        help_needed: str | None = None,
        task_id: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Log a struggle or challenge.

        Recording struggles helps:
        - Track problem-solving patterns
        - Create documentation for others
        - Get help if needed (help_needed field)
        - Remember solutions for similar problems

        Args:
            title: Struggle title
            what_struggled: What the challenge was
            attempted_solutions: What you tried (even if it didn't work)
            resolution: How it was resolved (if resolved)
            help_needed: What help you need (if unresolved)
            task_id: Optional related task
            tags: Optional list of tags

        Returns:
            Created struggle entry
        """
        async with httpx.AsyncClient() as client:
            payload = {
                "title": title,
                "what_struggled": what_struggled,
                "attempted_solutions": attempted_solutions or [],
                "resolution": resolution,
                "help_needed": help_needed,
                "task_id": task_id,
                "tags": tags or [],
            }

            resp = await client.post(
                f"{_get_api_url()}/journals/me/struggles",
                json=payload,
                headers={"X-Agent-Id": agent_id},
            )

            if resp.status_code not in [200, 201]:
                return _format_error_response(
                    "CREATE_FAILED",
                    "Failed to create struggle entry",
                    {"api_error": resp.text},
                )

            entry = resp.json()

        guidance = "Struggle recorded."
        if help_needed and not resolution:
            guidance += " Since you indicated help is needed, consider asking in your cell channel."

        return {
            "status": "created",
            "entry": entry,
            "guidance": guidance,
        }

    # =========================================================================
    # SEARCH
    # =========================================================================

    @mcp.tool()
    async def roboco_journal_search(
        query: str,
        top_k: int = 5,
    ) -> dict[str, Any]:
        """
        Search your past journal entries.

        Uses semantic search to find relevant entries based on meaning,
        not just keywords. Great for:
        - Finding past decisions on similar topics
        - Recalling how you solved similar problems
        - Getting context from previous work

        Args:
            query: What to search for
            top_k: Maximum results to return (default 5)

        Returns:
            Matching journal entries
        """
        async with httpx.AsyncClient() as client:
            payload = {
                "query": query,
                "top_k": min(top_k, 20),  # Cap at 20
            }

            resp = await client.post(
                f"{_get_api_url()}/journals/me/search",
                json=payload,
                headers={"X-Agent-Id": agent_id},
            )

            if resp.status_code != status.HTTP_200_OK:
                return _format_error_response(
                    "SEARCH_FAILED",
                    "Failed to search journal",
                    {"api_error": resp.text},
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

    # =========================================================================
    # STATS
    # =========================================================================

    @mcp.tool()
    async def roboco_journal_stats() -> dict[str, Any]:
        """
        Get statistics about your journal.

        Returns counts by entry type, growth metrics, and other stats.
        Useful for reflection and tracking your development.

        Returns:
            Journal statistics
        """
        async with httpx.AsyncClient() as client:
            # Get basic stats
            stats_resp = await client.get(
                f"{_get_api_url()}/journals/me/stats",
                headers={"X-Agent-Id": agent_id},
            )

            # Get growth metrics
            growth_resp = await client.get(
                f"{_get_api_url()}/journals/me/growth",
                headers={"X-Agent-Id": agent_id},
            )

            stats = (
                stats_resp.json()
                if stats_resp.status_code == status.HTTP_200_OK
                else {}
            )
            growth = (
                growth_resp.json()
                if growth_resp.status_code == status.HTTP_200_OK
                else {}
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

    # =========================================================================
    # LIST RECENT
    # =========================================================================

    @mcp.tool()
    async def roboco_journal_recent(
        entry_type: str | None = None,
        task_id: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """
        List recent journal entries.

        Args:
            entry_type:
                Optional filter by type
                (general, task_reflection, decision_log, learning, struggle)
            task_id:
                Optional filter by related task
            limit:
                Maximum entries to return

        Returns:
            Recent journal entries
        """
        async with httpx.AsyncClient() as client:
            params: dict[str, Any] = {"limit": min(limit, 50)}
            if entry_type:
                params["entry_type"] = entry_type
            if task_id:
                params["task_id"] = task_id

            resp = await client.get(
                f"{_get_api_url()}/journals/me/entries",
                params=params,
                headers={"X-Agent-Id": agent_id},
            )

            if resp.status_code != status.HTTP_200_OK:
                return _format_error_response(
                    "LIST_FAILED",
                    "Failed to list entries",
                )

            entries = resp.json()

        return {
            "entries": entries,
            "count": len(entries),
        }

    return mcp


# =============================================================================
# STANDALONE RUNNER
# =============================================================================

if __name__ == "__main__":
    import sys

    two = 2

    if len(sys.argv) < two:
        print("Usage: python journal_server.py <agent_id>")
        sys.exit(1)

    agent_id = sys.argv[1]
    server = create_journal_mcp_server(agent_id)
    server.run()
