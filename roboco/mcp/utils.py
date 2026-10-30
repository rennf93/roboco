"""
MCP Server Utilities

Shared HTTP helpers used by the surviving MCP servers (`optimal_server.py`
and `docs_server.py`). Older callers (`task_server`, `journal_server`,
`message_server`, `notify_server`, `a2a_server`, `project_server`) were
deleted in Phase 4 T9; their helpers (agent UUID resolution + cache,
`format_success_response`, etc.) were dropped along with them.
"""

import os
import sys
from typing import Any

import httpx
import structlog

from roboco.agents_config import get_agent_role, get_agent_team
from roboco.config import settings

# HTTP success range used by ApiResponse.ok (200 ≤ status < 300).
_HTTP_SUCCESS_MIN = 200
_HTTP_SUCCESS_MAX = 300

# Default timeout for API calls (seconds).
DEFAULT_TIMEOUT = 30.0

# roboco.security's guard middleware blocks with plain text, not JSON: 400
# "Suspicious activity detected" (WAF), 403 "Forbidden" / "IP has been
# banned" (IP list / ban), 429 (rate limit), verified live, no
# content-type header at all, response.json() raises JSONDecodeError.
_GUARD_BLOCK_STATUS_CODES = frozenset({400, 403, 429})


def guard_blocked_envelope(
    status_code: int, text: str, path: str
) -> dict[str, Any] | None:
    """A ``gateway_blocked`` Envelope for a guard-blocked request, else None.

    Both flow_server._post and do_server._post call this from the same spot
    their generic "no JSON body" transport_error branch used to catch alone:
    a guard block IS a "no JSON body" response, and without this check the
    agent was told the orchestrator looked down when the request never even
    reached the route handler.
    """
    if status_code not in _GUARD_BLOCK_STATUS_CODES:
        return None
    return {
        "error": "gateway_blocked",
        "message": (
            f"security gateway rejected the request to {path} with HTTP"
            f" {status_code}: {text[:200]}"
        ),
        "remediate": (
            "the security gateway rejected this request's content (it reads"
            " as shell/SQL/HTML-like, or you are rate-limited/banned)."
            " Move any shell/SQL/HTML-looking text into a free-text verb"
            " field (evidence, notes, findings) and retry once; escalate to"
            " the human operator if it repeats."
        ),
        "missing": [],
    }


def configure_stdio_logging() -> None:
    """Route structlog output to stderr.

    Without this, structlog falls back to its un-configured default, a
    PrintLogger on stdout. The stdio transport requires stdout to carry
    only JSON-RPC frames, so any log line emitted during a server's
    lifetime would corrupt the protocol stream.

    Call this only from a stdio MCP server's own process. A module that
    another process also imports must guard the call (e.g. under
    ``if __name__ == "__main__":``, before ``mcp.run()``), never call it
    at plain module scope - the reconfiguration is global structlog
    state and would otherwise leak into the importer too.
    """
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))


def _get_agent_headers(agent_id: str) -> dict[str, str]:
    """
    Build the standard headers ApiClient sends with every request.

    Returns headers dict with X-Agent-ID, X-Agent-Role, optionally
    X-Agent-Team, and X-Agent-Token when ROBOCO_AGENT_TOKEN is set in the
    environment (injected by the orchestrator at spawn time).

    The API middleware verifies token == HMAC(agent_id:role:team,
    ROBOCO_AGENT_AUTH_SECRET), which stops an agent on the Docker network
    from spoofing another agent's role via header.
    """
    headers = {
        "X-Agent-ID": agent_id,
        "X-Agent-Role": get_agent_role(agent_id),
    }
    team = get_agent_team(agent_id)
    if team:
        headers["X-Agent-Team"] = team
    token = os.environ.get("ROBOCO_AGENT_TOKEN")
    if token and token != "UNSIGNED":
        headers["X-Agent-Token"] = token
    return headers


def format_error_response(
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    hint: str | None = None,
) -> dict[str, Any]:
    """
    Format a standardized error response for MCP tools.

    Uses the common error_response format for consistency with API layer.

    Args:
        code: Error code (e.g., "NOT_FOUND", "API_ERROR", "PERMISSION_DENIED")
        message: Human-readable error message
        details: Optional additional error details
        hint: Optional RAG search suggestion for finding solutions

    Returns:
        Standardized error response dict with status="error"
    """
    from roboco.api.schemas.common import error_response

    return error_response(code, message, details, hint)


# =============================================================================
# API CLIENT
# =============================================================================


class ApiResponse:
    """Wrapper for API response with convenience methods."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    @property
    def ok(self) -> bool:
        """True if status code indicates success (2xx)."""
        return (
            self._response.status_code >= _HTTP_SUCCESS_MIN
            and self._response.status_code < _HTTP_SUCCESS_MAX
        )

    @property
    def status_code(self) -> int:
        """HTTP status code."""
        return self._response.status_code

    def json(self) -> Any:
        """Parse response as JSON."""
        return self._response.json()

    @property
    def text(self) -> str:
        """Response body as text."""
        return self._response.text

    def is_status(self, *codes: int) -> bool:
        """Check if status matches any of the given codes."""
        return self._response.status_code in codes


class ApiClient:
    """
    HTTP client for MCP servers to call the internal API.

    Provides:
    - Connection pooling via shared httpx.AsyncClient
    - Automatic URL building from endpoint paths
    - Automatic agent header injection
    - Standardized error response formatting
    - Configurable timeouts

    Usage:
        client = ApiClient(agent_id="be-dev-1")

        # GET request
        resp = await client.get("/tasks/123")
        if resp.ok:
            task = resp.json()

        # POST request
        resp = await client.post("/tasks", json={"title": "New task"})

        # With custom timeout
        resp = await client.get("/slow-endpoint", timeout=60.0)
    """

    def __init__(
        self,
        agent_id: str,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        """
        Initialize API client.

        Args:
            agent_id: Agent identifier for header injection
            timeout: Default request timeout in seconds
        """
        self.agent_id = agent_id
        self.timeout = timeout
        self.base_url = settings.internal_api_url
        self._client: httpx.AsyncClient | None = None

    def _get_headers(self) -> dict[str, str]:
        """Get headers with agent context."""
        return _get_agent_headers(self.agent_id)

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Get or create the httpx client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def close(self) -> None:
        """Close the HTTP client. Call when done with the client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    def _build_url(self, endpoint: str) -> str:
        """Build full URL from endpoint path."""
        # Remove leading slash if present to avoid double slashes
        if endpoint.startswith("/"):
            endpoint = endpoint[1:]
        return f"{self.base_url}/{endpoint}"

    async def get(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> ApiResponse:
        """
        Make GET request.

        Args:
            endpoint: API endpoint path (e.g., "/tasks/123")
            params: Query parameters
            timeout: Override default timeout

        Returns:
            ApiResponse wrapper
        """
        client = await self._ensure_client()
        resp = await client.get(
            self._build_url(endpoint),
            params=params,
            headers=self._get_headers(),
            timeout=timeout or self.timeout,
        )
        return ApiResponse(resp)

    async def post(
        self,
        endpoint: str,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> ApiResponse:
        """
        Make POST request.

        Args:
            endpoint: API endpoint path
            json: JSON body
            params: Query parameters
            timeout: Override default timeout

        Returns:
            ApiResponse wrapper
        """
        client = await self._ensure_client()
        resp = await client.post(
            self._build_url(endpoint),
            json=json,
            params=params,
            headers=self._get_headers(),
            timeout=timeout or self.timeout,
        )
        return ApiResponse(resp)

    async def put(
        self,
        endpoint: str,
        json: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> ApiResponse:
        """
        Make PUT request.

        Args:
            endpoint: API endpoint path
            json: JSON body
            timeout: Override default timeout

        Returns:
            ApiResponse wrapper
        """
        client = await self._ensure_client()
        resp = await client.put(
            self._build_url(endpoint),
            json=json,
            headers=self._get_headers(),
            timeout=timeout or self.timeout,
        )
        return ApiResponse(resp)

    async def patch(
        self,
        endpoint: str,
        json: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> ApiResponse:
        """
        Make PATCH request (partial update).

        Args:
            endpoint: API endpoint path
            json: JSON body with fields to update
            timeout: Override default timeout

        Returns:
            ApiResponse wrapper
        """
        client = await self._ensure_client()
        resp = await client.patch(
            self._build_url(endpoint),
            json=json,
            headers=self._get_headers(),
            timeout=timeout or self.timeout,
        )
        return ApiResponse(resp)

    async def delete(
        self,
        endpoint: str,
        timeout: float | None = None,
    ) -> ApiResponse:
        """
        Make DELETE request.

        Args:
            endpoint: API endpoint path
            timeout: Override default timeout

        Returns:
            ApiResponse wrapper
        """
        client = await self._ensure_client()
        resp = await client.delete(
            self._build_url(endpoint),
            headers=self._get_headers(),
            timeout=timeout or self.timeout,
        )
        return ApiResponse(resp)

    # =========================================================================
    # CONVENIENCE METHODS
    # =========================================================================

    async def get_or_error(
        self,
        endpoint: str,
        error_code: str = "API_ERROR",
        error_message: str = "Request failed",
        params: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """
        GET request returning (data, error) tuple.

        Args:
            endpoint: API endpoint
            error_code: Error code if request fails
            error_message: Error message if request fails
            params: Query parameters

        Returns:
            (json_data, None) on success, (None, error_response) on failure
        """
        try:
            resp = await self.get(endpoint, params=params)
            if resp.ok:
                return resp.json(), None
            return None, format_error_response(
                error_code,
                error_message,
                {"status": resp.status_code, "detail": resp.text},
            )
        except Exception as e:
            return None, format_error_response(
                error_code,
                error_message,
                {"exception": str(e)},
            )

    async def post_or_error(
        self,
        endpoint: str,
        json: dict[str, Any] | None = None,
        error_code: str = "API_ERROR",
        error_message: str = "Request failed",
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """
        POST request returning (data, error) tuple.

        Args:
            endpoint: API endpoint
            json: JSON body
            error_code: Error code if request fails
            error_message: Error message if request fails

        Returns:
            (json_data, None) on success, (None, error_response) on failure
        """
        try:
            resp = await self.post(endpoint, json=json)
            if resp.ok:
                return resp.json(), None
            return None, format_error_response(
                error_code,
                error_message,
                {"status": resp.status_code, "detail": resp.text},
            )
        except Exception as e:
            return None, format_error_response(
                error_code,
                error_message,
                {"exception": str(e)},
            )
