"""Unit tests for roboco.services.llm.probe_ollama_tags.

Covers all five branches of the function:
  1. Successful JSON parse → returns model names list
  2. httpx.TimeoutException → returns ([], timeout message)
  3. httpx.ConnectError → returns ([], connect-error message)
  4. httpx.HTTPStatusError → returns ([], http-status message)
  5. Generic Exception → logs server-side and returns ([], generic error string)

All tests mock `httpx.AsyncClient` so they require no network or DB.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from roboco.services.llm import probe_ollama_tags

_BASE_URL = "http://localhost:11434"


@pytest.mark.asyncio
async def test_probe_ollama_tags_success() -> None:
    """Successful /api/tags response returns list of model name strings."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "models": [
            {"name": "llama3.1:8b"},
            {"name": "gemma2:9b"},
            {"name": "qwen2.5:14b"},
        ]
    }

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("roboco.services.llm.httpx.AsyncClient", return_value=mock_client):
        models, error = await probe_ollama_tags(_BASE_URL)

    assert error is None
    assert models == ["llama3.1:8b", "gemma2:9b", "qwen2.5:14b"]


@pytest.mark.asyncio
async def test_probe_ollama_tags_timeout() -> None:
    """httpx.TimeoutException returns ([], message) mentioning the timeout."""
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("roboco.services.llm.httpx.AsyncClient", return_value=mock_client):
        models, error = await probe_ollama_tags(_BASE_URL)

    assert models == []
    assert error is not None
    assert "timed out" in error.lower() or "timeout" in error.lower()


@pytest.mark.asyncio
async def test_probe_ollama_tags_connect_error() -> None:
    """httpx.ConnectError returns ([], message) mentioning connection failure."""
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("roboco.services.llm.httpx.AsyncClient", return_value=mock_client):
        models, error = await probe_ollama_tags(_BASE_URL)

    assert models == []
    assert error is not None
    assert "connect" in error.lower() or "offline" in error.lower()


@pytest.mark.asyncio
async def test_probe_ollama_tags_http_status_error() -> None:
    """httpx.HTTPStatusError returns ([], message) containing the HTTP status code."""
    mock_response = MagicMock()
    mock_response.status_code = 503

    http_err = httpx.HTTPStatusError(
        "service unavailable",
        request=MagicMock(),
        response=mock_response,
    )

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=http_err)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("roboco.services.llm.httpx.AsyncClient", return_value=mock_client):
        models, error = await probe_ollama_tags(_BASE_URL)

    assert models == []
    assert error is not None
    assert "503" in error


@pytest.mark.asyncio
async def test_probe_ollama_tags_generic_exception_logs_and_returns_generic() -> None:
    """Generic Exception logs the error server-side and returns a generic string.

    The raw exception message must NOT appear in the returned error string
    (to avoid leaking internal server details in HTTP responses).
    """
    secret_detail = "internal secret detail that must not leak"

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=RuntimeError(secret_detail))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("roboco.services.llm.httpx.AsyncClient", return_value=mock_client),
        patch("roboco.services.llm._log") as mock_log,
    ):
        models, error = await probe_ollama_tags(_BASE_URL)

    assert models == []
    assert error is not None
    # The returned error string must NOT contain the raw exception text.
    assert secret_detail not in error
    # The logger must have been called to record the exception server-side.
    mock_log.error.assert_called_once()
