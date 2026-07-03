"""
Documentation MCP Server

Exposes documentation file management tools to Claude Code agents.
Bypasses Claude Code's file permission system by going through the API.

Tools:
- roboco_docs_write: Write/update documentation (RAG-based deduplication)
- roboco_docs_read: Read a documentation file
- roboco_docs_list: List documentation files
- roboco_docs_delete: Delete a documentation file
"""

from typing import Any

from mcp.server.fastmcp import FastMCP

from roboco.mcp.schemas import WriteDocInput
from roboco.mcp.utils import ApiClient, format_error_response

# =============================================================================
# HANDLER FUNCTIONS
# =============================================================================


async def _handle_write(
    data: WriteDocInput,
    client: ApiClient,
) -> dict[str, Any]:
    """Handle documentation write via API."""
    payload = {
        "task_id": data.task_id,
        "filename": data.filename,
        "doc_type": data.doc_type,
        "title": data.title,
        "content": data.content,
    }

    result, error = await client.post_or_error(
        "/docs/write",
        json=payload,
        error_code="WRITE_FAILED",
        error_message="Failed to write documentation",
    )
    if error or result is None:
        return error or format_error_response("WRITE_FAILED", "No result")

    status = result.get("status", "created")
    path = result.get("path")
    is_update = status == "updated"

    if is_update:
        guidance = (
            f"Updated existing documentation at /app/docs/{path}. "
            "RAG found similar doc and updated it instead of creating duplicate."
        )
    else:
        guidance = (
            f"Created new documentation at /app/docs/{path}. "
            "The file has been indexed in RAG and linked to the task."
        )

    # Surface the repo-commit outcome (#34): a 'failed' commit means the doc
    # saved to /app/docs but did NOT reach the project repo — the cell PM must
    # be told so the PR does not ship without the docs.
    doc_ref = result.get("doc_ref") or {}
    commit_status = doc_ref.get("commit_status") if isinstance(doc_ref, dict) else None
    if commit_status == "failed":
        guidance += (
            " WARNING: the doc could NOT be committed to the project repo — tell"
            " the cell PM so the docs are not missing from the PR."
        )
    elif commit_status == "skipped":
        guidance += " (saved to /app/docs only — no task branch to commit onto yet)."

    return {
        "status": status,
        "path": path,
        "doc_ref": doc_ref,
        "is_update": is_update,
        "guidance": guidance,
    }


async def _handle_read(
    path: str,
    client: ApiClient,
) -> dict[str, Any]:
    """Handle documentation read via API."""
    result, error = await client.get_or_error(
        "/docs/read",
        params={"path": path},
        error_code="READ_FAILED",
        error_message="Failed to read documentation",
    )
    if error or result is None:
        return error or format_error_response("READ_FAILED", "No result")

    return {
        "path": result.get("path"),
        "content": result.get("content"),
        "size_bytes": result.get("size_bytes"),
    }


async def _handle_list(
    task_id: str | None,
    client: ApiClient,
) -> dict[str, Any]:
    """Handle documentation listing via API."""
    params = {}
    if task_id:
        params["task_id"] = task_id

    result, error = await client.get_or_error(
        "/docs/list",
        params=params if params else None,
        error_code="LIST_FAILED",
        error_message="Failed to list documentation",
    )
    if error or result is None:
        return error or format_error_response("LIST_FAILED", "No result")

    return {
        "documents": result.get("documents", []),
        "team": result.get("team"),
        "count": result.get("count", 0),
        "guidance": (
            f"Found {result.get('count', 0)} documentation files. "
            "Use roboco_docs_read to view contents."
        ),
    }


async def _handle_delete(
    path: str,
    client: ApiClient,
) -> dict[str, Any]:
    """Handle documentation deletion via API."""
    resp = await client.delete(f"/docs/delete?path={path}")

    if not resp.ok:
        return format_error_response(
            "DELETE_FAILED",
            f"Failed to delete documentation: {resp.text}",
        )

    return {
        "status": "deleted",
        "path": path,
        "guidance": f"Documentation at {path} has been deleted.",
    }


# =============================================================================
# MCP SERVER FACTORY
# =============================================================================


def create_docs_mcp_server(agent_id: str) -> FastMCP:
    """
    Create a Documentation MCP server for a specific agent.

    Args:
        agent_id: The agent identifier (e.g., "be-doc")

    Returns:
        Configured FastMCP server
    """
    mcp = FastMCP(f"roboco-docs-{agent_id}", json_response=True)
    client = ApiClient(agent_id)

    @mcp.tool()
    async def roboco_docs_write(data: WriteDocInput) -> dict[str, Any]:
        """
        Write or update documentation for your current task.

        SCOPE — TEAM-FACING DOCS ONLY: this tool writes into docs/<team>/...
        (api/qa/guide/readme/changelog/architecture/design), which is NEVER
        published to the docs site. It is for internal notes future agents
        need, not for anything a user should read.

        User-facing docs (anything meant to ship at docs.roboco.tech) do NOT
        go through this tool at all — author them as a normal task in the
        'roboco-website' project instead: MDX under src/content/docs/, a
        route wrapper under src/app/docs/, and a src/content/docs/nav.ts
        entry (the 3-edit pattern; a CI check fails the PR if any of the
        three is missing). Calling this tool with doc_type="user_facing" is
        refused with this same guidance rather than silently accepted.

        SMART DEDUPLICATION: Before creating a new doc, RAG searches for
        existing documentation with similar CONTENT (not just title).
        If a high-similarity match is found, the existing doc is updated
        instead of creating a duplicate.

        Team folder is determined automatically from your agent ID.
        Subfolder is determined by doc_type.

        The document will be:
        - Checked against existing docs via content similarity search
        - Updated if semantically similar doc exists, or created if new
        - Tracked in task.documents for database traceability
        - Indexed in RAG for searchability

        Args:
            data: WriteDocInput with task_id, filename, doc_type, title, content

        Returns:
            status: "created" or "updated"
            is_update: True if existing doc was updated
        """
        return await _handle_write(data, client)

    @mcp.tool()
    async def roboco_docs_read(path: str) -> dict[str, Any]:
        """
        Read a documentation file by path.

        Args:
            path: Normalized path (e.g., "backend/api/endpoints.md")
        """
        return await _handle_read(path, client)

    @mcp.tool()
    async def roboco_docs_list(task_id: str | None = None) -> dict[str, Any]:
        """
        List documentation files.

        If task_id is provided, lists docs for that task.
        Otherwise, lists all docs for your team.

        Args:
            task_id: Optional task UUID to filter by
        """
        return await _handle_list(task_id, client)

    @mcp.tool()
    async def roboco_docs_delete(path: str) -> dict[str, Any]:
        """
        Delete a documentation file.

        Args:
            path: Normalized path (e.g., "backend/api/endpoints.md")
        """
        return await _handle_delete(path, client)

    return mcp


# =============================================================================
# STANDALONE RUNNER
# =============================================================================

if __name__ == "__main__":
    import sys

    MIN_ARGS = 2
    if len(sys.argv) < MIN_ARGS:
        print("Usage: python docs_server.py <agent_id>")
        sys.exit(1)

    agent_id_arg = sys.argv[1]
    server = create_docs_mcp_server(agent_id_arg)
    server.run()
