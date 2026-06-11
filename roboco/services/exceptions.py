"""
LLM Service Exceptions

Shared exception types and helpers for LLM provider rate-limit handling.
Importable from a single location as required by the acceptance criteria.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Maximum number of retries on HTTP 429 / provider RateLimitError.
MAX_RATE_LIMIT_RETRIES: int = 5

#: HTTP status code for rate limiting.
HTTP_TOO_MANY_REQUESTS: int = 429


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RateLimitError(Exception):
    """Raised when an LLM provider returns a 429 rate-limit response after all retries.

    Attributes:
        provider:    Name of the provider that rate-limited us (``"anthropic"`` /
                     ``"ollama"``).
        retry_after: The last ``Retry-After`` value seen (in seconds), or ``None``
                     if the header was absent.
    """

    def __init__(
        self,
        provider: str,
        retry_after: float | None = None,
    ) -> None:
        self.provider = provider
        self.retry_after = retry_after
        msg = f"Rate limit exceeded for provider '{provider}'"
        if retry_after is not None:
            msg += f"; retry after {retry_after:.1f}s"
        super().__init__(msg)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"RateLimitError("
            f"provider={self.provider!r}, retry_after={self.retry_after!r})"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_retry_after_header(response: httpx.Response) -> float | None:
    """Extract the ``Retry-After`` header from an httpx response as seconds.

    The header is treated as a plain integer/float number of seconds.  HTTP-date
    format is not supported (LLM providers invariably use numeric values).

    Returns:
        Number of seconds to wait, or ``None`` if the header is absent or cannot
        be parsed as a number.
    """
    header = response.headers.get("retry-after")
    if not header:
        return None
    try:
        return float(header)
    except (ValueError, TypeError):
        return None
