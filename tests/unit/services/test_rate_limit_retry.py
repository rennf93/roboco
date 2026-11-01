"""
Unit tests for rate-limit retry behaviour in the Ollama embedder and the
Anthropic extraction call.

The text-generation call sites (index ``ask``, the Mentor, the validator) no
longer retry HTTP 429 themselves: they go through the background LLM client,
which falls back from Ollama to Claude Haiku instead (see
tests/unit/services/test_background_llm*.py).

Covers acceptance criteria:
- 5-retry exhaustion raises RateLimitError
- Retry-After header drives the sleep duration
- Partial retries then success returns the correct result
- ConnectError / TimeoutException in OllamaEmbedder does NOT trigger the 429
  retry path (the two concerns are composed without double-retrying)
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import anthropic as anthropic_mod
import httpx
import pytest

# ---------------------------------------------------------------------------
# Module imports
# ---------------------------------------------------------------------------
from roboco.models.extraction import ExtractionContext
from roboco.services.exceptions import (
    MAX_RATE_LIMIT_RETRIES,
    RateLimitError,
    parse_retry_after_header,
)
from roboco.services.extraction import ExtractionService
from roboco.services.optimal_brain.ollama_embedder import (
    MAX_RETRIES,
    OllamaConnectionError,
    OllamaEmbedder,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_RETRY_AFTER_FLOAT = 30.0
_RETRY_AFTER_FLOAT_2 = 2.5
_RETRY_AFTER_7 = 7.0
_RETRY_AFTER_9 = 9.0
_RETRY_AFTER_12 = 12.0
_RETRY_AFTER_5 = 5.0
# Zero-vector dimension in test responses
_EMBED_DIM = 4
# 2 rate-limit errors then 1 success
_CALLS_2RL_1_SUCCESS = 3

_EMBED_PATH = (
    "roboco.services.optimal_brain.ollama_embedder.OllamaEmbedder._create_async_client"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(
    status_code: int,
    body: Any = None,
    retry_after: str | None = None,
) -> httpx.Response:
    """Build a minimal httpx.Response for use in mocks."""
    headers: dict[str, str] = {}
    if retry_after is not None:
        headers["retry-after"] = retry_after
    content = json.dumps(body or {}).encode()
    return httpx.Response(
        status_code=status_code,
        headers=headers,
        content=content,
    )


def _success_embed_response(n: int = 1) -> httpx.Response:
    """Return a valid Ollama /api/embed response with *n* zero-vectors."""
    body = {"embeddings": [[0.0] * _EMBED_DIM] * n}
    return _make_response(200, body)


def _429_response(retry_after: str | None = None) -> httpx.Response:
    return _make_response(429, {"error": "rate limited"}, retry_after=retry_after)


def _make_async_client_mock(post_return: Any = None) -> AsyncMock:
    """Return an async context manager mock exposing `.post`."""
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    if post_return is not None:
        client.post = AsyncMock(return_value=post_return)
    return client


def _make_extraction_context() -> ExtractionContext:
    return ExtractionContext(
        content="This is a test message that is long enough.",
        agent_id=uuid4(),
        channel_id=uuid4(),
        session_id=uuid4(),
        group_id=uuid4(),
    )


def _make_anthropic_rl_exc(
    retry_after: str | None = None,
) -> anthropic_mod.RateLimitError:
    """Build a minimal anthropic.RateLimitError."""
    fake_resp = MagicMock()
    fake_resp.headers = {} if retry_after is None else {"retry-after": retry_after}
    return anthropic_mod.RateLimitError(
        message="rate limit",
        response=fake_resp,
        body={},
    )


# ===========================================================================
# 1. parse_retry_after_header
# ===========================================================================


class TestParseRetryAfterHeader:
    def test_integer_seconds(self) -> None:
        resp = _429_response(retry_after="30")
        assert parse_retry_after_header(resp) == _RETRY_AFTER_FLOAT

    def test_float_seconds(self) -> None:
        resp = _429_response(retry_after="2.5")
        assert parse_retry_after_header(resp) == _RETRY_AFTER_FLOAT_2

    def test_missing_header_returns_none(self) -> None:
        resp = _make_response(429)
        assert parse_retry_after_header(resp) is None

    def test_non_numeric_returns_none(self) -> None:
        resp = _429_response(retry_after="Wed, 21 Oct 2015 07:28:00 GMT")
        assert parse_retry_after_header(resp) is None


# ===========================================================================
# 2. RateLimitError class
# ===========================================================================


class TestRateLimitError:
    def test_fields(self) -> None:
        err = RateLimitError(provider="anthropic", retry_after=_RETRY_AFTER_FLOAT)
        assert err.provider == "anthropic"
        assert err.retry_after == _RETRY_AFTER_FLOAT

    def test_message_includes_provider(self) -> None:
        err = RateLimitError(provider="ollama")
        assert "ollama" in str(err)

    def test_message_includes_retry_after_when_set(self) -> None:
        err = RateLimitError(provider="ollama", retry_after=15.0)
        assert "15" in str(err)

    def test_none_retry_after(self) -> None:
        err = RateLimitError(provider="anthropic", retry_after=None)
        assert err.retry_after is None


# ===========================================================================
# 3. OllamaEmbedder - async path (aembed_query)
# ===========================================================================


class TestOllamaEmbedderAembed:
    """Tests for the async aembed_query method."""

    def _make_embedder(self) -> OllamaEmbedder:
        return OllamaEmbedder(base_url="http://ollama-test:11434")

    async def test_five_consecutive_429s_raise_rate_limit_error(self) -> None:
        """After MAX_RATE_LIMIT_RETRIES attempts all 429 -> RateLimitError."""
        embedder = self._make_embedder()
        mock_c = _make_async_client_mock(post_return=_429_response())

        with (
            patch(_EMBED_PATH, return_value=mock_c),
            patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(RateLimitError) as exc_info,
        ):
            await embedder.aembed_query("hello")

        assert exc_info.value.provider == "ollama"
        assert mock_c.post.call_count == MAX_RATE_LIMIT_RETRIES

    async def test_retry_after_header_respected_as_sleep_duration(self) -> None:
        """Retry-After: 7 -> asyncio.sleep(7.0) on each inter-attempt gap."""
        embedder = self._make_embedder()
        sleep_calls: list[float] = []

        async def _fake_sleep(secs: float) -> None:
            sleep_calls.append(secs)

        mock_c = _make_async_client_mock(post_return=_429_response(retry_after="7"))

        with (
            patch(_EMBED_PATH, return_value=mock_c),
            patch("asyncio.sleep", side_effect=_fake_sleep),
            pytest.raises(RateLimitError),
        ):
            await embedder.aembed_query("hello")

        assert all(s == _RETRY_AFTER_7 for s in sleep_calls)
        assert len(sleep_calls) == MAX_RATE_LIMIT_RETRIES - 1

    async def test_partial_retries_then_success(self) -> None:
        """Two 429s then a 200 -> returns the embedding list."""
        embedder = self._make_embedder()
        mock_c = AsyncMock()
        mock_c.__aenter__ = AsyncMock(return_value=mock_c)
        mock_c.__aexit__ = AsyncMock(return_value=False)
        mock_c.post = AsyncMock(
            side_effect=[
                _429_response(),
                _429_response(),
                _success_embed_response(),
            ]
        )

        with (
            patch(_EMBED_PATH, return_value=mock_c),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await embedder.aembed_query("hello")

        assert isinstance(result, list)
        assert len(result) == _EMBED_DIM

    async def test_connect_error_does_not_trigger_429_retry_path(
        self,
    ) -> None:
        """ConnectError -> OllamaConnectionError after MAX_RETRIES=3, not 5."""
        embedder = self._make_embedder()
        mock_c = AsyncMock()
        mock_c.__aenter__ = AsyncMock(return_value=mock_c)
        mock_c.__aexit__ = AsyncMock(return_value=False)
        mock_c.post = AsyncMock(side_effect=httpx.ConnectError("refused"))

        with (
            patch(_EMBED_PATH, return_value=mock_c),
            patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(OllamaConnectionError),
        ):
            await embedder.aembed_query("hello")

        assert mock_c.post.call_count == MAX_RETRIES

    async def test_timeout_does_not_trigger_429_retry_path(self) -> None:
        """TimeoutException -> OllamaConnectionError after MAX_RETRIES=3."""
        embedder = self._make_embedder()
        mock_c = AsyncMock()
        mock_c.__aenter__ = AsyncMock(return_value=mock_c)
        mock_c.__aexit__ = AsyncMock(return_value=False)
        mock_c.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))

        with (
            patch(_EMBED_PATH, return_value=mock_c),
            patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(OllamaConnectionError),
        ):
            await embedder.aembed_query("hello")

        assert mock_c.post.call_count == MAX_RETRIES


# ===========================================================================
# 4. OllamaEmbedder - sync path (embed_query)
# ===========================================================================


class TestOllamaEmbedderSync:
    """Tests for the synchronous embed_query method."""

    def _make_embedder(self) -> OllamaEmbedder:
        return OllamaEmbedder(base_url="http://ollama-test:11434")

    def test_five_consecutive_429s_raise_rate_limit_error(self) -> None:
        embedder = self._make_embedder()
        mock_client = MagicMock()
        mock_client.post.return_value = _429_response()

        with (
            patch.object(embedder, "_get_sync_client", return_value=mock_client),
            patch("time.sleep"),
            pytest.raises(RateLimitError) as exc_info,
        ):
            embedder.embed_query("hello")

        assert exc_info.value.provider == "ollama"
        assert mock_client.post.call_count == MAX_RATE_LIMIT_RETRIES

    def test_retry_after_header_respected_as_sleep_duration(self) -> None:
        embedder = self._make_embedder()
        sleep_calls: list[float] = []

        mock_client = MagicMock()
        mock_client.post.return_value = _429_response(retry_after="9")

        with (
            patch.object(embedder, "_get_sync_client", return_value=mock_client),
            patch("time.sleep", side_effect=sleep_calls.append),
            pytest.raises(RateLimitError),
        ):
            embedder.embed_query("hello")

        assert all(s == _RETRY_AFTER_9 for s in sleep_calls)
        assert len(sleep_calls) == MAX_RATE_LIMIT_RETRIES - 1

    def test_partial_retries_then_success(self) -> None:
        embedder = self._make_embedder()
        mock_client = MagicMock()
        mock_client.post.side_effect = [_429_response(), _success_embed_response()]

        with (
            patch.object(embedder, "_get_sync_client", return_value=mock_client),
            patch("time.sleep"),
        ):
            result = embedder.embed_query("hello")

        assert isinstance(result, list)

    def test_connect_error_does_not_trigger_rate_limit_retry(self) -> None:
        embedder = self._make_embedder()
        mock_client = MagicMock()
        mock_client.post.side_effect = httpx.ConnectError("refused")

        with (
            patch.object(embedder, "_get_sync_client", return_value=mock_client),
            patch("time.sleep"),
            pytest.raises(OllamaConnectionError),
        ):
            embedder.embed_query("hello")

        assert mock_client.post.call_count == MAX_RETRIES


# ===========================================================================
# 5. OllamaEmbedder - _embed_batch_sync
# ===========================================================================


class TestOllamaEmbedBatchSync:
    def _make_embedder(self) -> OllamaEmbedder:
        return OllamaEmbedder(base_url="http://ollama-test:11434")

    def test_five_429s_raise_rate_limit_error(self) -> None:
        embedder = self._make_embedder()
        mock_client = MagicMock()
        mock_client.post.return_value = _429_response()

        with patch("time.sleep"), pytest.raises(RateLimitError):
            embedder._embed_batch_sync(mock_client, ["a", "b"], batch_index=0)

        assert mock_client.post.call_count == MAX_RATE_LIMIT_RETRIES

    def test_connect_error_raises_connection_error_not_rate_limit(self) -> None:
        embedder = self._make_embedder()
        mock_client = MagicMock()
        mock_client.post.side_effect = httpx.ConnectError("refused")

        with patch("time.sleep"), pytest.raises(OllamaConnectionError):
            embedder._embed_batch_sync(mock_client, ["a"], batch_index=0)

        assert mock_client.post.call_count == MAX_RETRIES


# ===========================================================================
# 6. extraction.py - Anthropic rate-limit retry
# ===========================================================================


class TestExtractionAnthropicRetry:
    """Tests for ExtractionService.extract_with_llm Anthropic retry logic.

    AsyncAnthropic is imported *inside* extract_with_llm, so we patch at the
    anthropic module level. The client is NOT used as a context manager there.
    """

    async def test_five_rate_limit_errors_raises_rate_limit_error(self) -> None:
        """RateLimitError raised 5 times -> our RateLimitError propagated."""
        svc = ExtractionService()
        api_exc = _make_anthropic_rl_exc(retry_after="5")

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(side_effect=api_exc)

        with (
            patch("anthropic.AsyncAnthropic") as mock_cls,
            patch("roboco.config.settings") as mock_settings,
            patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(RateLimitError) as exc_info,
        ):
            mock_cls.return_value = mock_client
            mock_settings.anthropic_api_key = "test-key"
            await svc.extract_with_llm(_make_extraction_context())

        assert exc_info.value.provider == "anthropic"
        assert mock_client.messages.create.call_count == MAX_RATE_LIMIT_RETRIES

    async def test_retry_after_header_drives_sleep_duration(self) -> None:
        """Retry-After: 12 -> asyncio.sleep(12) called on each gap."""
        svc = ExtractionService()
        sleep_calls: list[float] = []
        api_exc = _make_anthropic_rl_exc(retry_after="12")

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(side_effect=api_exc)

        async def _fake_sleep(secs: float) -> None:
            sleep_calls.append(secs)

        with (
            patch("anthropic.AsyncAnthropic") as mock_cls,
            patch("roboco.config.settings") as mock_settings,
            patch("asyncio.sleep", side_effect=_fake_sleep),
            pytest.raises(RateLimitError),
        ):
            mock_cls.return_value = mock_client
            mock_settings.anthropic_api_key = "test-key"
            await svc.extract_with_llm(_make_extraction_context())

        assert all(s == _RETRY_AFTER_12 for s in sleep_calls)
        assert len(sleep_calls) == MAX_RATE_LIMIT_RETRIES - 1

    async def test_partial_rate_limit_then_success_returns_result(self) -> None:
        """Two RateLimitErrors then success -> result returned, no raise."""
        svc = ExtractionService()
        api_exc = _make_anthropic_rl_exc()

        text_block = MagicMock()
        text_block.text = "[N,]{type,content,confidence}:\nreasoning,Hello world,0.9"
        success_response = MagicMock()
        success_response.content = [text_block]

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            side_effect=[api_exc, api_exc, success_response]
        )

        raised = False
        with (
            patch("anthropic.AsyncAnthropic") as mock_cls,
            patch("roboco.config.settings") as mock_settings,
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_cls.return_value = mock_client
            mock_settings.anthropic_api_key = "test-key"
            try:
                result = await svc.extract_with_llm(_make_extraction_context())
                assert result is not None
            except RateLimitError:
                raised = True

        assert not raised, "RateLimitError raised even though 3rd attempt succeeded"
        assert mock_client.messages.create.call_count == _CALLS_2RL_1_SUCCESS
