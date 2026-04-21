"""
MCP Server Utilities

Shared utilities for all MCP servers to avoid code duplication.
Contains common functions for:
- Agent header generation
- Error response formatting
- Agent UUID resolution with caching
- API client for internal API calls
- Shared caches for cross-server data
"""

import os
from typing import Any

import httpx

from roboco.agents_config import get_agent_role, get_agent_team
from roboco.config import settings

# =============================================================================
# SHARED CACHES (Module-level for cross-request persistence)
# =============================================================================

# Agent UUID cache: maps slug -> UUID for quick lookups
# This cache is shared across all MCP servers to avoid redundant API calls
_agent_uuid_cache: dict[str, str] = {}

# UUID format constants
_UUID_LENGTH = 36  # Standard UUID string length
_UUID_HYPHEN_COUNT = 4  # Number of hyphens in a UUID

# HTTP status code ranges
_HTTP_OK = 200
_HTTP_SUCCESS_MIN = 200
_HTTP_SUCCESS_MAX = 300  # exclusive

# Default timeout for API calls (seconds)
DEFAULT_TIMEOUT = 30.0


def get_agent_headers(agent_id: str) -> dict[str, str]:
    """
    Get standard headers for API calls from an MCP server.

    Args:
        agent_id: The agent's identifier (slug or UUID)

    Returns:
        Headers dict with X-Agent-ID, X-Agent-Role, optionally X-Agent-Team,
        and X-Agent-Token when ROBOCO_AGENT_TOKEN is set in the
        environment (injected by the orchestrator at spawn time).
    """
    headers = {
        "X-Agent-ID": agent_id,
        "X-Agent-Role": get_agent_role(agent_id),
    }
    team = get_agent_team(agent_id)
    if team:
        headers["X-Agent-Team"] = team
    # Auth token — orchestrator sets this per-agent in the container env.
    # The API middleware verifies token == HMAC(agent_id:role:team,
    # ROBOCO_AGENT_AUTH_SECRET), which stops an agent on the Docker
    # network from spoofing another agent's role via header.
    token = os.environ.get("ROBOCO_AGENT_TOKEN")
    if token:
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


def format_success_response(
    data: Any,
    guidance: str | None = None,
    next_step: str | None = None,
) -> dict[str, Any]:
    """
    Format a standardized success response for MCP tools.

    Uses the common success_response format for consistency with API layer.

    Args:
        data: Response payload
        guidance: Actionable next step guidance
        next_step: Workflow hint (e.g., PLAN, EXECUTE)

    Returns:
        Standardized success response dict with status="success"
    """
    from roboco.api.schemas.common import success_response

    return success_response(data, guidance, next_step)


async def resolve_agent_uuid(
    agent_id: str,
    headers: dict[str, str],
) -> str | None:
    """
    Resolve an agent identifier to its UUID.

    If the agent_id is already a UUID, returns it directly.
    Otherwise, looks up the agent by slug/name via the API.

    Args:
        agent_id: Agent identifier (slug like "be-dev-1" or UUID string)
        headers: Request headers for API authentication

    Returns:
        UUID string if found, None otherwise
    """
    # If it looks like a UUID already, return it
    if len(agent_id) == _UUID_LENGTH and agent_id.count("-") == _UUID_HYPHEN_COUNT:
        return agent_id

    # Look up by slug - GET /agents/{id} accepts both UUID and slug
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        try:
            resp = await client.get(
                f"{settings.internal_api_url}/agents/{agent_id}",
                headers=headers,
            )
            if resp.status_code == _HTTP_OK:
                data = resp.json()
                agent_id_result: str | None = data.get("id")
                return agent_id_result
        except Exception:
            pass
    return None


async def resolve_agent_uuid_cached(
    agent_id: str,
    client: "ApiClient",
) -> str | None:
    """
    Resolve an agent identifier to its UUID with caching.

    This is the preferred method for MCP servers as it caches results
    to avoid redundant API calls across tool invocations.

    Args:
        agent_id: Agent identifier (slug like "be-dev-1" or UUID string)
        client: ApiClient instance for making API calls

    Returns:
        UUID string if found, None otherwise
    """
    # Check cache first
    if agent_id in _agent_uuid_cache:
        return _agent_uuid_cache[agent_id]

    # Resolve using the non-cached method
    result = await resolve_agent_uuid(agent_id, client._get_headers())

    # Cache the result if found
    if result:
        _agent_uuid_cache[agent_id] = result

    return result


def clear_agent_uuid_cache() -> None:
    """Clear the agent UUID cache. Useful for testing."""
    _agent_uuid_cache.clear()


def get_cached_agent_uuid(agent_id: str) -> str | None:
    """
    Get cached agent UUID without making API call.

    Returns:
        Cached UUID if available, None otherwise
    """
    return _agent_uuid_cache.get(agent_id)


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
        return get_agent_headers(self.agent_id)

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
