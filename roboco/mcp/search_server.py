"""Web Research MCP Server.

Exposes ``web_search`` and ``web_fetch`` to Board + PM agents. Both tools call
the backend ``/research/*`` routes, which hold the provider API key server-side
— the key is never present in the agent container, and the agent never makes an
external request itself. Mounted conditionally per role by the orchestrator
(see ``_generate_mcp_config``); the route re-checks the role as defence in depth.
"""

from typing import Any

from mcp.server.fastmcp import FastMCP

from roboco.mcp.utils import ApiClient, format_error_response

_NOT_CONFIGURED = (
    "Web research is not configured on this deployment (no provider key). "
    "Proceed without external sources or ask the CEO to set one."
)


async def _handle_search(
    query: str, max_results: int | None, client: ApiClient
) -> dict[str, Any]:
    """Run a web search via the backend and shape the result for the agent."""
    payload: dict[str, Any] = {"query": query}
    if max_results is not None:
        payload["max_results"] = max_results

    result, error = await client.post_or_error(
        "/research/search",
        json=payload,
        error_code="SEARCH_FAILED",
        error_message="Web search failed",
    )
    if error or result is None:
        return error or format_error_response("SEARCH_FAILED", "No result")

    provider = result.get("provider", "unknown")
    results = result.get("results", [])
    if provider == "null":
        guidance = _NOT_CONFIGURED
    else:
        guidance = (
            f"{len(results)} result(s) from '{provider}'. Cite the URL for any "
            "fact you use, and persist findings with note(scope='reflect', ...) "
            "so the team keeps the source."
        )
    return {
        "query": result.get("query", query),
        "provider": provider,
        "answer": result.get("answer"),
        "results": results,
        "guidance": guidance,
    }


async def _handle_fetch(
    url: str, max_chars: int | None, client: ApiClient
) -> dict[str, Any]:
    """Extract readable page content via the backend provider."""
    payload: dict[str, Any] = {"url": url}
    if max_chars is not None:
        payload["max_chars"] = max_chars

    result, error = await client.post_or_error(
        "/research/fetch",
        json=payload,
        error_code="FETCH_FAILED",
        error_message="Web fetch failed",
    )
    if error or result is None:
        return error or format_error_response("FETCH_FAILED", "No result")

    return {
        "url": result.get("url", url),
        "provider": result.get("provider", "unknown"),
        "content": result.get("content", ""),
        "truncated": result.get("truncated", False),
    }


def create_search_mcp_server(agent_id: str) -> FastMCP:
    """Create a Web Research MCP server bound to a specific agent."""
    mcp = FastMCP(f"roboco-search-{agent_id}", json_response=True)
    client = ApiClient(agent_id)

    @mcp.tool()
    async def web_search(query: str, max_results: int | None = None) -> dict[str, Any]:
        """Search the public web for market/competitor/technical information.

        Returns cited results (title, url, snippet) and, where the provider
        supports it, a short synthesized answer. Use this for research the
        knowledge base can't answer — competitors, pricing, libraries, trends.
        Always cite the URL for anything you rely on, and persist key findings
        with a note so the team retains the source.

        Args:
            query: The search query.
            max_results: Optional cap on results (clamped to the server limit).
        """
        return await _handle_search(query, max_results, client)

    @mcp.tool()
    async def web_fetch(url: str, max_chars: int | None = None) -> dict[str, Any]:
        """Fetch the readable content of a specific web page.

        Uses the configured provider's content-extraction endpoint, so it works
        only with providers that support extraction (Tavily, Exa). Content is
        truncated to the server's character cap.

        Args:
            url: The page URL to extract.
            max_chars: Optional cap on returned characters (clamped server-side).
        """
        return await _handle_fetch(url, max_chars, client)

    return mcp


if __name__ == "__main__":
    import sys

    MIN_ARGS = 2
    if len(sys.argv) < MIN_ARGS:
        print("Usage: python search_server.py <agent_id>")
        sys.exit(1)

    server = create_search_mcp_server(sys.argv[1])
    server.run()
