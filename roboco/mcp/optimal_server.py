"""
Optimal MCP Server (Optimal Brain)

Exposes knowledge base, RAG, and semantic search tools to Claude Code agents.

Core Tools:
- roboco_kb_search: Semantic search across indexed content
- roboco_rag_query: RAG query with answer generation
- roboco_kb_index_code: Index code files (PM/Developer)
- roboco_kb_index_docs: Index documentation (PM/Documenter)
- roboco_kb_stats: Get index statistics
- roboco_tokens_estimate: Estimate token count for content

Optimal Brain Tools:
- roboco_ask_mentor: Conversational RAG with follow-up context
- roboco_search_error: Search for known error solutions
- roboco_record_error_solution: Record how an error was solved
- roboco_check_decision: Check for similar past decisions
- roboco_record_decision: Record an architectural decision
- roboco_get_standards: Get coding/security/workflow standards
- roboco_validate_action: Validate action against standards
"""

import os
from typing import Any

from fastapi import status as http_status
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from roboco.mcp.utils import ApiClient, format_error_response


class RecordDecisionInput(BaseModel):
    """Input model for recording a decision."""

    topic: str = Field(..., description="What was decided")
    decision: str = Field(..., description="The choice made")
    rationale: str = Field(..., description="Why this choice was made")
    alternatives: list[dict[str, Any]] | None = Field(
        None, description="Other options considered [{name, pros, cons}]"
    )
    context: str = Field("", description="Additional context about the decision")
    scope: str = Field("team", description="'team' or 'org'")
    tags: list[str] | None = Field(None, description="Tags for categorization")


# Legacy aliases agents commonly pass that are not valid IndexType values.
# The only valid value for documentation is 'documentation' — 'docs' raises
# ValueError at the route's IndexType(...) conversion, yielding a 400.
_INDEX_TYPE_ALIASES = {"docs": "documentation"}


def normalize_index_types(index_types: list[str] | None) -> list[str] | None:
    """Map legacy index-type aliases to valid IndexType values.

    Agents (and our own docstrings) historically used 'docs', which is not a
    member of the ``IndexType`` enum. Translate it to 'documentation' before
    the request leaves the client so the route's ``IndexType(...)`` conversion
    succeeds instead of 400-ing.
    """
    if index_types is None:
        return None
    return [_INDEX_TYPE_ALIASES.get(t, t) for t in index_types]


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
            index_types: Index types to search (documentation, decisions,
                learnings, standards, errors, reviews, journals, conversations)

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
        normalized_index_types = normalize_index_types(index_types)
        if normalized_index_types:
            payload["index_types"] = normalized_index_types

        resp = await client.post("/optimal/kb/search", json=payload)
        if not resp.ok:
            return format_error_response(
                "SEARCH_FAILED",
                "Failed to search knowledge base",
                {"api_error": resp.text},
                hint="Try roboco_ask_mentor(question) for AI-synthesized answers.",
            )

        result = resp.json()
        total = result.get("total", 0)
        response: dict[str, Any] = {
            "status": "success",
            "query": query,
            "total": total,
            "results": result.get("results", []),
        }
        if total == 0:
            response["hint"] = (
                "No results. Try roboco_ask_mentor(question) for better answers."
            )
        return response

    @mcp.tool()
    async def roboco_rag_query(
        query: str,
        top_k: int = 5,
        project: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """
        RAG query - get an AI-generated answer using knowledge base context.

        NOTE: For most questions, prefer `roboco_ask_mentor` instead!
        The mentor searches multiple indexes and supports follow-up questions.

        Use this simpler tool only when you need:
        - A quick answer from a specific index type
        - To filter by project or task_id

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

        # RAG queries can take longer due to LLM call - use 65s timeout
        resp = await client.post("/optimal/rag/query", json=payload, timeout=65.0)
        if not resp.ok:
            return format_error_response(
                "RAG_FAILED",
                "Failed to query RAG",
                {"api_error": resp.text},
                hint="Try roboco_ask_mentor(question) instead - it's more robust.",
            )

        result = resp.json()
        answer = result.get("answer", "")
        context_used = result.get("context_used", 0)
        response: dict[str, Any] = {
            "status": "success",
            "query": query,
            "answer": answer,
            "citations": result.get("citations", []),
            "context_used": context_used,
        }
        # Guide to mentor for better results
        if context_used == 0 or "couldn't find" in answer.lower():
            response["hint"] = (
                "Limited results. roboco_ask_mentor(question) searches more sources "
                "and supports follow-up questions."
            )
        return response

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


# =========================================================================
# OPTIMAL BRAIN TOOLS
# =========================================================================


def _register_mentor_tools(mcp: FastMCP, client: ApiClient) -> None:
    """Register mentor (conversational RAG) tools."""

    @mcp.tool()
    async def roboco_ask_mentor(
        question: str,
        conversation_id: str | None = None,
        domain: str | None = None,
    ) -> dict[str, Any]:
        """
        Ask the organizational knowledge base for help.

        THIS IS THE PRIMARY TOOL for knowledge base questions.
        Use this instead of roboco_rag_query for most questions.

        This is a conversational interface - you can ask follow-up questions
        by providing the conversation_id from a previous response.

        The mentor searches across:
        - Standards & guidelines
        - Past architectural decisions
        - Team learnings and reflections
        - Codebase patterns
        - Known error solutions

        Args:
            question: Your question (natural language)
            conversation_id: Optional ID from previous response for follow-ups
            domain: Optional domain filter (coding, security, workflow)

        Returns:
            Answer with sources and suggested follow-up questions

        Example:
            First question:
            >>> roboco_ask_mentor("How do I handle authentication?")

            Follow-up:
            >>> roboco_ask_mentor(
            ...     "What about refresh tokens?",
            ...     conversation_id="abc-123"
            ... )
        """
        payload: dict[str, Any] = {"question": question}
        if conversation_id:
            payload["conversation_id"] = conversation_id
        if domain:
            payload["domain"] = domain

        # Mentor uses LLM - allow 65s timeout
        resp = await client.post("/optimal/mentor/ask", json=payload, timeout=65.0)
        if not resp.ok:
            return format_error_response(
                "MENTOR_FAILED",
                "Failed to get mentor response",
                {"api_error": resp.text},
            )

        result = resp.json()
        return {
            "status": "success",
            "answer": result.get("answer", ""),
            "sources": result.get("sources", []),
            "conversation_id": result.get("conversation_id", ""),
            "suggested_followups": result.get("suggested_followups", []),
        }


def _register_error_tools(mcp: FastMCP, client: ApiClient) -> None:
    """Register error pattern tools."""

    @mcp.tool()
    async def roboco_search_error(
        error_message: str,
        context: str = "",
    ) -> dict[str, Any]:
        """
        Search for known solutions to an error.

        Before debugging from scratch, check if someone already solved this!
        The error database is global - all agents learn from all errors.

        Args:
            error_message: The error message you're seeing
            context: Additional context (what you were doing, file, etc.)

        Returns:
            Known solutions ranked by relevance, with worked/not-worked status
        """
        payload: dict[str, Any] = {"error_message": error_message}
        if context:
            payload["context"] = context

        resp = await client.post("/optimal/errors/search", json=payload)
        if not resp.ok:
            return format_error_response(
                "SEARCH_FAILED",
                "Failed to search errors",
                {"api_error": resp.text},
            )

        result = resp.json()
        solutions_found = len(result.get("results", []))
        response: dict[str, Any] = {
            "status": "success",
            "error_message": error_message,
            "solutions_found": solutions_found,
            "results": result.get("results", []),
        }
        if solutions_found == 0:
            response["hint"] = (
                "No known solutions. Try roboco_ask_mentor(f'How do I fix: {error}') "
                "for guidance. If you solve it, use roboco_record_error_solution() "
                "to help future agents."
            )
        return response

    @mcp.tool()
    async def roboco_record_error_solution(
        error_message: str,
        context: str,
        solution: str,
        worked: bool = True,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Record how you solved an error for future agents.

        When you solve an error, record it! Future agents (including yourself)
        will benefit from this knowledge.

        Args:
            error_message: The error message that was encountered
            context: What you were doing when the error occurred
            solution: How you fixed it
            worked: Whether the solution actually worked (default: True)
            tags: Optional tags for categorization

        Returns:
            Confirmation of recorded error pattern
        """
        if not error_message or not solution:
            return format_error_response(
                "INVALID_INPUT",
                "Both error_message and solution are required",
            )

        payload: dict[str, Any] = {
            "error_message": error_message,
            "context": context,
            "solution": solution,
            "worked": worked,
        }
        if tags:
            payload["tags"] = tags

        resp = await client.post("/optimal/errors/record", json=payload)
        if not resp.ok:
            return format_error_response(
                "RECORD_FAILED",
                "Failed to record error solution",
                {"api_error": resp.text},
            )

        return {
            "status": "recorded",
            "message": "Error solution recorded for future agents",
            "error_id": resp.json().get("error_id", ""),
        }


def _register_decision_tools(mcp: FastMCP, client: ApiClient) -> None:
    """Register decision memory tools."""

    @mcp.tool()
    async def roboco_check_decision(
        topic: str,
    ) -> dict[str, Any]:
        """
        Check if a similar decision was made before.

        Before making an architectural or design decision, check for precedents!
        This maintains consistency across the organization.

        Args:
            topic: What you're deciding about (e.g., "authentication method",
                   "database choice", "API design pattern")

        Returns:
            Similar past decisions with rationale and alternatives considered
        """
        resp = await client.post(
            "/optimal/decisions/check",
            json={"topic": topic},
        )
        if not resp.ok:
            return format_error_response(
                "CHECK_FAILED",
                "Failed to check decisions",
                {"api_error": resp.text},
            )

        result = resp.json()
        has_precedent = len(result.get("decisions", [])) > 0
        return {
            "status": "success",
            "topic": topic,
            "has_precedent": has_precedent,
            "decisions": result.get("decisions", []),
            "recommendation": result.get("recommendation", ""),
        }

    @mcp.tool()
    async def roboco_record_decision(params: RecordDecisionInput) -> dict[str, Any]:
        """
        Record an architectural or design decision.

        Document important decisions for future reference. This helps
        maintain consistency and provides context for future changes.

        Args:
            params: RecordDecisionInput containing:
                - topic: What was decided (e.g., "authentication method")
                - decision: The choice made (e.g., "JWT with refresh tokens")
                - rationale: Why this choice was made
                - alternatives: Other options considered [{name, pros, cons}]
                - context: Additional context about the decision
                - scope: "team" or "org" (default: team)
                - tags: Optional tags for categorization

        Returns:
            Confirmation with decision ID
        """
        payload: dict[str, Any] = {
            "topic": params.topic,
            "decision": params.decision,
            "rationale": params.rationale,
            "scope": params.scope,
        }
        if params.alternatives:
            payload["alternatives"] = params.alternatives
        if params.context:
            payload["context"] = params.context
        if params.tags:
            payload["tags"] = params.tags

        resp = await client.post("/optimal/decisions/record", json=payload)
        if not resp.ok:
            return format_error_response(
                "RECORD_FAILED",
                "Failed to record decision",
                {"api_error": resp.text},
            )

        return {
            "status": "recorded",
            "message": "Decision recorded for organizational memory",
            "decision_id": resp.json().get("decision_id", ""),
        }


def _register_standards_tools(mcp: FastMCP, client: ApiClient) -> None:
    """Register standards and validation tools."""

    @mcp.tool()
    async def roboco_get_standards(
        domain: str,
        language: str | None = None,
    ) -> dict[str, Any]:
        """
        Get coding/security/workflow standards for a domain.

        Use this BEFORE writing code to ensure you follow team standards.

        Args:
            domain: Domain to get standards for (coding, security, workflow)
            language: Optional language filter (python, typescript)

        Returns:
            Relevant standards with severity levels
        """
        payload: dict[str, Any] = {"domain": domain}
        if language:
            payload["language"] = language

        resp = await client.post("/optimal/standards/get", json=payload)
        if not resp.ok:
            return format_error_response(
                "FETCH_FAILED",
                "Failed to get standards",
                {"api_error": resp.text},
            )

        result = resp.json()
        return {
            "status": "success",
            "domain": domain,
            "language": language,
            "standards": result.get("standards", []),
            "total": len(result.get("standards", [])),
        }

    @mcp.tool()
    async def roboco_validate_action(
        action_type: str,
        context: str,
    ) -> dict[str, Any]:
        """
        Validate an action against organizational standards.

        Check if what you're about to do follows team rules.

        Args:
            action_type: Type of action (e.g., "create_endpoint", "add_dependency")
            context: Details about what you're doing (code snippet, etc.)

        Returns:
            Validation result with any violations or warnings
        """
        if not action_type or not context:
            return format_error_response(
                "INVALID_INPUT",
                "action_type and context are required",
            )

        payload: dict[str, Any] = {
            "action_type": action_type,
            "context": context,
        }

        resp = await client.post("/optimal/standards/validate", json=payload)
        if not resp.ok:
            return format_error_response(
                "VALIDATE_FAILED",
                "Failed to validate action",
                {"api_error": resp.text},
            )

        result = resp.json()
        return {
            "status": "validated",
            "allowed": result.get("allowed", True),
            "violations": result.get("violations", []),
            "warnings": result.get("warnings", []),
            "relevant_standards": result.get("relevant_standards", []),
        }

    @mcp.tool()
    async def roboco_review_code(
        code: str,
        file_path: str,
        change_type: str = "modify",
    ) -> dict[str, Any]:
        """
        Review code and get feedback before committing.

        Get automated code review feedback based on:
        - Team coding standards
        - Security policies
        - Past review comments on similar code
        - Known error patterns

        Args:
            code: The code to review
            file_path: Path to the file being reviewed
            change_type: Type of change (add, modify, delete)

        Returns:
            Review result with comments, score, and approval status
        """
        if not code or not file_path:
            return format_error_response(
                "INVALID_INPUT",
                "code and file_path are required",
            )

        payload: dict[str, Any] = {
            "code": code,
            "file_path": file_path,
            "change_type": change_type,
        }

        resp = await client.post("/optimal/review/code", json=payload)
        if not resp.ok:
            return format_error_response(
                "REVIEW_FAILED",
                "Failed to review code",
                {"api_error": resp.text},
            )

        result = resp.json()
        return {
            "status": "reviewed",
            "approved": result.get("approved", True),
            "score": result.get("score", 100),
            "comments": result.get("comments", []),
            "standards_checked": result.get("standards_checked", []),
            "similar_reviews": result.get("similar_reviews", []),
        }


def _register_learning_tools(mcp: FastMCP, client: ApiClient) -> None:
    """Register learning tools."""

    @mcp.tool()
    async def roboco_record_learning(
        content: str,
        category: str,
        team: str | None = None,
        shareable: bool = True,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Record a learning for cross-agent knowledge sharing.

        When you learn something useful, record it! Future agents (including
        yourself) will benefit from this knowledge.

        Args:
            content: What you learned (be specific and actionable)
            category: Category (error_handling, performance, testing, pattern,
                      architecture, security, workflow)
            team: Optional team filter (backend, frontend, ux_ui)
            shareable: Share with other agents? (default: True)
            tags: Optional tags for categorization

        Returns:
            Confirmation with learning ID
        """
        if not content or not category:
            return format_error_response(
                "INVALID_INPUT",
                "Both content and category are required",
            )

        payload: dict[str, Any] = {
            "content": content,
            "category": category,
            "shareable": shareable,
        }
        if team:
            payload["team"] = team
        if tags:
            payload["tags"] = tags

        resp = await client.post("/optimal/learnings/record", json=payload)
        if not resp.ok:
            return format_error_response(
                "RECORD_FAILED",
                "Failed to record learning",
                {"api_error": resp.text},
            )

        return {
            "status": "recorded",
            "message": "Learning recorded for future agents",
            "learning_id": resp.json().get("learning_id", ""),
        }

    @mcp.tool()
    async def roboco_search_learnings(
        query: str,
        category: str | None = None,
        team: str | None = None,
        top_k: int = 10,
    ) -> dict[str, Any]:
        """
        Search for relevant learnings from other agents.

        Before starting a task, check what others have learned!

        Args:
            query: Search query
            category: Optional category filter
            team: Optional team filter
            top_k: Number of results (default: 10)

        Returns:
            Matching learnings with relevance scores
        """
        payload: dict[str, Any] = {"query": query, "top_k": top_k}
        if category:
            payload["category"] = category
        if team:
            payload["team"] = team

        resp = await client.post("/optimal/learnings/search", json=payload)
        if not resp.ok:
            return format_error_response(
                "SEARCH_FAILED",
                "Failed to search learnings",
                {"api_error": resp.text},
            )

        result = resp.json()
        total = result.get("total", 0)
        response: dict[str, Any] = {
            "status": "success",
            "query": query,
            "total": total,
            "results": result.get("results", []),
        }
        if total == 0:
            response["hint"] = (
                "No learnings found. Try roboco_ask_mentor(question) for broader "
                "knowledge. If you learn something useful, use "
                "roboco_record_learning() to share it."
            )
        return response


def _register_index_management_tools(mcp: FastMCP, client: ApiClient) -> None:
    """Register index management tools for administrative operations."""

    @mcp.tool()
    async def roboco_clear_index(index_type: str) -> dict[str, Any]:
        """
        Clear all documents from a specific index.

        Use with caution - this permanently deletes indexed content.
        Useful for recovering from corrupted indexes or starting fresh.

        PERMISSION: Requires CLEAR_INDEX permission.

        Args:
            index_type: One of: code, documentation, conversations, journals,
                        errors, standards, decisions, reviews, learnings

        Returns:
            Confirmation of cleared index
        """
        valid_types = {
            "code",
            "documentation",
            "conversations",
            "journals",
            "errors",
            "standards",
            "decisions",
            "reviews",
            "learnings",
        }
        if index_type not in valid_types:
            return format_error_response(
                "INVALID_INDEX_TYPE",
                f"Invalid index type. Must be one of: {', '.join(sorted(valid_types))}",
            )

        resp = await client.delete(f"/optimal/kb/{index_type}")
        if not resp.ok:
            if resp.status_code == http_status.HTTP_403_FORBIDDEN:
                return format_error_response(
                    "NOT_AUTHORIZED",
                    "You don't have permission to clear indexes",
                )
            return format_error_response(
                "CLEAR_FAILED",
                "Failed to clear index",
                {"api_error": resp.text},
            )

        return {"status": "success", "cleared": index_type}

    @mcp.tool()
    async def roboco_reindex_all(force: bool = False) -> dict[str, Any]:
        """
        Trigger re-indexing of code and documentation.

        Re-scans the codebase and docs directories to update indexes.
        Useful when files have been added/changed outside of normal workflow.

        PERMISSION: Requires INDEX_CODE permission.

        Args:
            force: If True, reindex even if indexes aren't empty

        Returns:
            Count of indexed code files and documentation files
        """
        resp = await client.post(
            "/optimal/kb/reindex",
            params={"force": str(force).lower()},
        )
        if not resp.ok:
            if resp.status_code == http_status.HTTP_403_FORBIDDEN:
                return format_error_response(
                    "NOT_AUTHORIZED",
                    "You don't have permission to trigger reindexing",
                )
            return format_error_response(
                "REINDEX_FAILED",
                "Failed to trigger reindexing",
                {"api_error": resp.text},
            )

        result = resp.json()
        return {
            "status": "success",
            "code_files_indexed": result.get("code", 0),
            "docs_files_indexed": result.get("docs", 0),
        }

    @mcp.tool()
    async def roboco_index_status() -> dict[str, Any]:
        """
        Get detailed status of all indexes.

        Shows document counts and last update times for each index type.
        Useful for monitoring and debugging indexing issues.

        Returns:
            Status information for each index including document counts
        """
        resp = await client.get("/optimal/stats")
        if not resp.ok:
            return format_error_response(
                "STATS_FAILED",
                "Failed to get index status",
                {"api_error": resp.text},
            )

        result = resp.json()
        return {
            "status": "success",
            "initialized": result.get("initialized", False),
            "indexes": result.get("indexes", {}),
        }


def _register_proactive_tools(mcp: FastMCP, client: ApiClient) -> None:
    """Register proactive context tools."""

    @mcp.tool()
    async def roboco_get_proactive_context(
        task_id: str,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """
        Get proactive context for a task.

        First checks for stored context (injected when task was claimed).
        Falls back to generating fresh context if not available.

        Args:
            task_id: UUID of the task to get context for
            force_refresh: If True, skip stored context and generate fresh

        Returns:
            Dictionary with context categories and a summary
        """
        # Try to get stored context from task first (unless force_refresh)
        if not force_refresh:
            task_resp = await client.get(f"/tasks/{task_id}")
            if task_resp.ok:
                task_data = task_resp.json()
                stored_context = task_data.get("proactive_context")
                if stored_context and isinstance(stored_context, dict):
                    # Return stored context with source indicator
                    return {
                        "status": "success",
                        "source": "stored",
                        "task_id": task_id,
                        "similar_tasks": stored_context.get("similar_tasks", []),
                        "relevant_learnings": stored_context.get(
                            "relevant_learnings", []
                        ),
                        "code_patterns": stored_context.get("code_patterns", []),
                        "applicable_standards": stored_context.get(
                            "applicable_standards", []
                        ),
                        "recent_decisions": stored_context.get("recent_decisions", []),
                        "known_issues": stored_context.get("known_issues", []),
                        "summary": stored_context.get("summary", ""),
                    }

        # Fall back to generating fresh context
        payload = {"task_id": task_id}
        resp = await client.post("/optimal/context/proactive", json=payload)

        if not resp.ok:
            return format_error_response(
                "CONTEXT_FAILED",
                "Failed to get proactive context",
                {"api_error": resp.text},
            )

        result = resp.json()
        return {
            "status": "success",
            "source": "fresh",
            "task_id": result.get("task_id"),
            "similar_tasks": result.get("similar_tasks", []),
            "relevant_learnings": result.get("relevant_learnings", []),
            "code_patterns": result.get("code_patterns", []),
            "applicable_standards": result.get("applicable_standards", []),
            "recent_decisions": result.get("recent_decisions", []),
            "known_issues": result.get("known_issues", []),
            "summary": result.get("summary", ""),
        }


# Role-scoped tool groups, mirroring the manifest scoping flow/do already do.
# Every registered schema rides in each turn's context, so a role only carries
# the groups its duties use. A group absent from this map registers for every
# role; an unknown/unset role registers everything (fail-open to the previous
# behaviour) — except index management, which is destructive operator tooling
# (clear/reindex) and registers only under ROBOCO_ALLOW_FULL_TOOLSET.
_GROUP_ROLES: dict[str, frozenset[str]] = {
    "error": frozenset({"developer", "qa"}),
    "standards": frozenset({"developer", "qa", "documenter", "pr_reviewer"}),
    "decision": frozenset(
        {"cell_pm", "main_pm", "product_owner", "head_marketing", "auditor"}
    ),
    "indexing": frozenset({"documenter"}),
}


def _role_wants(group: str, role: str) -> bool:
    """True when `role` should carry `group`'s tool schemas."""
    allowed = _GROUP_ROLES.get(group)
    if allowed is None:
        return True
    if not role:
        return True
    return role in allowed


def create_optimal_mcp_server(agent_id: str) -> FastMCP:
    """Create an Optimal MCP server for a specific agent, scoped to its role."""
    mcp = FastMCP(f"roboco-optimal-{agent_id}", json_response=True)
    client = ApiClient(agent_id)
    role = os.environ.get("ROBOCO_AGENT_ROLE", "")
    full_toolset = bool(os.environ.get("ROBOCO_ALLOW_FULL_TOOLSET"))

    # Universal groups — every role reasons with search/mentor/learnings.
    _register_search_tools(mcp, client)
    _register_utility_tools(mcp, client)
    _register_mentor_tools(mcp, client)
    _register_learning_tools(mcp, client)
    _register_proactive_tools(mcp, client)

    # Duty-scoped groups.
    if full_toolset or _role_wants("indexing", role):
        _register_indexing_tools(mcp, client)
    if full_toolset or _role_wants("error", role):
        _register_error_tools(mcp, client)
    if full_toolset or _role_wants("decision", role):
        _register_decision_tools(mcp, client)
    if full_toolset or _role_wants("standards", role):
        _register_standards_tools(mcp, client)

    # Destructive index management (clear/reindex) — dev/test escape hatch only.
    if full_toolset:
        _register_index_management_tools(mcp, client)

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
