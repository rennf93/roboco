"""
Optimal MCP Server

Exposes knowledge base, RAG, and semantic search tools to Claude Code agents.

Tools:
- roboco_kb_search: Semantic search across indexed content
- roboco_rag_query: RAG query with answer generation
- roboco_kb_index_code: Index code files (PM/Developer)
- roboco_kb_index_docs: Index documentation (PM/Documenter)
- roboco_kb_stats: Get index statistics
- roboco_tokens_estimate: Estimate token count for content
"""

from typing import Any

from fastapi import status as http_status
from mcp.server.fastmcp import FastMCP

from roboco.mcp.utils import ApiClient, format_error_response


def _register_search_tools(mcp: FastMCP, client: ApiClient) -> None:
    """Register search tools available to all agents."""

    @mcp.tool()
    async def roboco_kb_search(
        query: str,
        top_k: int = 5,
        project: str | None = None,
        task_id: str | None = None,
        index_types: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Semantic search across indexed knowledge base.

        Use this to find relevant code, documentation, past decisions,
        or learnings that might help with your current task.

        Args:
            query: Natural language search query
            top_k: Number of results to return (1-20, default 5)
            project: Optional project filter
            task_id: Optional task filter
            index_types: Index types to search (code, docs, decisions, learnings)

        Returns:
            Search results with relevance scores and source info
        """
        payload: dict[str, Any] = {
            "query": query,
            "top_k": min(max(top_k, 1), 20),
        }
        if project:
            payload["project"] = project
        if task_id:
            payload["task_id"] = task_id
        if index_types:
            payload["index_types"] = index_types

        resp = await client.post("/optimal/kb/search", json=payload)
        if not resp.ok:
            return format_error_response(
                "SEARCH_FAILED",
                "Failed to search knowledge base",
                {"api_error": resp.text},
            )

        result = resp.json()
        return {
            "status": "success",
            "query": query,
            "total": result.get("total", 0),
            "results": result.get("results", []),
        }

    @mcp.tool()
    async def roboco_rag_query(
        query: str,
        top_k: int = 5,
        project: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """
        RAG query - get an AI-generated answer using knowledge base context.

        Use this when you need an answer synthesized from the knowledge base,
        not just search results. Good for questions like:
        - "How does authentication work in this codebase?"
        - "What's the pattern for error handling?"
        - "What decisions were made about the database schema?"

        Args:
            query: Natural language question
            top_k: Number of context chunks to use (1-20, default 5)
            project: Optional project filter
            task_id: Optional task filter

        Returns:
            Generated answer with citations to sources
        """
        payload: dict[str, Any] = {
            "query": query,
            "top_k": min(max(top_k, 1), 20),
        }
        if project:
            payload["project"] = project
        if task_id:
            payload["task_id"] = task_id

        resp = await client.post("/optimal/rag/query", json=payload)
        if not resp.ok:
            return format_error_response(
                "RAG_FAILED",
                "Failed to query RAG",
                {"api_error": resp.text},
            )

        result = resp.json()
        return {
            "status": "success",
            "query": query,
            "answer": result.get("answer", ""),
            "citations": result.get("citations", []),
            "context_used": result.get("context_used", 0),
        }

    @mcp.tool()
    async def roboco_kb_stats() -> dict[str, Any]:
        """
        Get knowledge base statistics.

        Shows what's indexed and available for search.

        Returns:
            Stats about indexed content by type
        """
        resp = await client.get("/optimal/stats")
        if not resp.ok:
            return format_error_response(
                "STATS_FAILED",
                "Failed to get KB stats",
                {"api_error": resp.text},
            )

        return {
            "status": "success",
            **resp.json(),
        }


def _register_indexing_tools(mcp: FastMCP, client: ApiClient) -> None:
    """Register indexing tools (permission-controlled at API level)."""

    @mcp.tool()
    async def roboco_kb_index_code(
        sources: list[str],
        project: str | None = None,
    ) -> dict[str, Any]:
        """
        Index code files for semantic search.

        PERMISSION: Requires INDEX_CODE permission (typically PM, Developer).

        Args:
            sources: List of file paths, directories, or globs (e.g., ["src/**/*.py"])
            project: Optional project identifier for filtering

        Returns:
            Count of indexed files
        """
        if not sources:
            return format_error_response(
                "INVALID_INPUT",
                "At least one source path required",
            )

        payload: dict[str, Any] = {"sources": sources}
        if project:
            payload["project"] = project

        resp = await client.post("/optimal/kb/index/code", json=payload)
        if not resp.ok:
            if resp.status_code == http_status.HTTP_403_FORBIDDEN:
                return format_error_response(
                    "NOT_AUTHORIZED",
                    "You don't have permission to index code",
                )
            return format_error_response(
                "INDEX_FAILED",
                "Failed to index code",
                {"api_error": resp.text},
            )

        result = resp.json()
        return {
            "status": "indexed",
            "indexed": result.get("indexed", 0),
            "sources": sources,
            "project": project,
        }

    @mcp.tool()
    async def roboco_kb_index_docs(
        sources: list[str],
        project: str | None = None,
    ) -> dict[str, Any]:
        """
        Index documentation for semantic search.

        PERMISSION: Requires INDEX_DOCS permission (typically PM, Documenter).

        Args:
            sources: List of file paths, URLs, or globs (e.g., ["docs/**/*.md"])
            project: Optional project identifier for filtering

        Returns:
            Count of indexed documents
        """
        if not sources:
            return format_error_response(
                "INVALID_INPUT",
                "At least one source path required",
            )

        payload: dict[str, Any] = {"sources": sources}
        if project:
            payload["project"] = project

        resp = await client.post("/optimal/kb/index/docs", json=payload)
        if not resp.ok:
            if resp.status_code == http_status.HTTP_403_FORBIDDEN:
                return format_error_response(
                    "NOT_AUTHORIZED",
                    "You don't have permission to index documentation",
                )
            return format_error_response(
                "INDEX_FAILED",
                "Failed to index documentation",
                {"api_error": resp.text},
            )

        result = resp.json()
        return {
            "status": "indexed",
            "indexed": result.get("indexed", 0),
            "sources": sources,
            "project": project,
        }


def _register_utility_tools(mcp: FastMCP, client: ApiClient) -> None:
    """Register utility tools."""

    @mcp.tool()
    async def roboco_tokens_estimate(
        content: str,
        model: str = "claude-sonnet-4-20250514",
    ) -> dict[str, Any]:
        """
        Estimate token count for content.

        Use this to check if content will fit within context limits.

        Args:
            content: Text content to estimate
            model: Model to estimate for (default: claude-sonnet-4)

        Returns:
            Token count estimate
        """
        if not content:
            return format_error_response(
                "INVALID_INPUT",
                "Content cannot be empty",
            )

        resp = await client.post(
            "/optimal/tokens/estimate",
            json={"content": content, "model": model},
        )
        if not resp.ok:
            return format_error_response(
                "ESTIMATE_FAILED",
                "Failed to estimate tokens",
                {"api_error": resp.text},
            )

        result = resp.json()
        return {
            "status": "success",
            "token_count": result.get("token_count", 0),
            "model": model,
            "content_length": len(content),
        }


def create_optimal_mcp_server(agent_id: str) -> FastMCP:
    """Create an Optimal MCP server for a specific agent."""
    mcp = FastMCP(f"roboco-optimal-{agent_id}", json_response=True)
    client = ApiClient(agent_id)

    # Register all tool groups
    _register_search_tools(mcp, client)
    _register_indexing_tools(mcp, client)
    _register_utility_tools(mcp, client)

    return mcp


if __name__ == "__main__":
    import sys

    _MIN_ARGS = 2
    if len(sys.argv) < _MIN_ARGS:
        print("Usage: python -m roboco.mcp.optimal_server <agent_id>")
        sys.exit(1)

    agent_id_cli = sys.argv[1]
    server = create_optimal_mcp_server(agent_id_cli)
    server.run()
