"""XClient coverage: OAuth1 signing, NullXClient no-op, LiveXClient calls."""

from __future__ import annotations

import re

import httpx
import pytest
from roboco.services.x_client import (
    MAX_TWEET_CHARS,
    LiveXClient,
    NullXClient,
    build_x_client,
    oauth1_authorization_header,
)
from roboco.services.x_credentials import XCredentialsData

_CREDS = XCredentialsData(
    api_key="xvz1evFS4wEEPTGEFPHBog",
    api_secret="kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw",
    access_token="370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMYzoCgnCU0r",
    access_token_secret="LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE",
)
_EXPECTED_LIKE_COUNT = 3


def test_null_client_is_unconfigured_and_never_raises() -> None:
    client = build_x_client(None, account_user_id="", timeout=5.0)
    assert isinstance(client, NullXClient)
    assert client.configured is False


@pytest.mark.asyncio
async def test_null_client_post_tweet_is_a_noop() -> None:
    client: NullXClient = NullXClient()
    result = await client.post_tweet("hello")
    assert result.posted is False
    assert result.tweet_id is None


@pytest.mark.asyncio
async def test_null_client_fetch_mentions_returns_empty() -> None:
    client: NullXClient = NullXClient()
    mentions = await client.fetch_mentions(since_id=None, max_results=10)
    assert mentions == []


def test_build_x_client_with_creds_returns_live_client() -> None:
    client = build_x_client(_CREDS, account_user_id="123", timeout=5.0)
    assert isinstance(client, LiveXClient)
    assert client.configured is True


def test_oauth1_header_has_expected_shape() -> None:
    header = oauth1_authorization_header(
        "POST", "https://api.twitter.com/2/tweets", _CREDS
    )
    assert header.startswith("OAuth ")
    for key in (
        "oauth_consumer_key",
        "oauth_nonce",
        "oauth_signature_method",
        "oauth_timestamp",
        "oauth_token",
        "oauth_version",
        "oauth_signature",
    ):
        assert f'{key}="' in header
    assert 'oauth_signature_method="HMAC-SHA1"' in header


def test_oauth1_header_signature_is_deterministic_for_fixed_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A known nonce + timestamp must always produce the same signature —
    proves the HMAC-SHA1 base-string construction is stable/reproducible."""
    monkeypatch.setattr("secrets.token_hex", lambda _n: "kYjzVBB8Y0ZFabxSWbWovY")
    monkeypatch.setattr("time.time", lambda: 1318622958.0)
    header_1 = oauth1_authorization_header(
        "GET", "https://api.twitter.com/2/users/me", _CREDS
    )
    monkeypatch.setattr("secrets.token_hex", lambda _n: "kYjzVBB8Y0ZFabxSWbWovY")
    monkeypatch.setattr("time.time", lambda: 1318622958.0)
    header_2 = oauth1_authorization_header(
        "GET", "https://api.twitter.com/2/users/me", _CREDS
    )
    assert header_1 == header_2
    signature_1 = re.search(r'oauth_signature="([^"]+)"', header_1)
    assert signature_1 is not None
    assert len(signature_1.group(1)) > 0


def test_oauth1_header_query_params_affect_signature() -> None:
    header_a = oauth1_authorization_header(
        "GET",
        "https://api.twitter.com/2/users/123/mentions",
        _CREDS,
        {"max_results": "5"},
    )
    header_b = oauth1_authorization_header(
        "GET",
        "https://api.twitter.com/2/users/123/mentions",
        _CREDS,
        {"max_results": "10"},
    )
    sig_a = re.search(r'oauth_signature="([^"]+)"', header_a)
    sig_b = re.search(r'oauth_signature="([^"]+)"', header_b)
    assert sig_a is not None
    assert sig_b is not None
    assert sig_a.group(1) != sig_b.group(1)  # different signed params -> different sig


@pytest.mark.asyncio
async def test_live_client_post_tweet_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"].startswith("OAuth ")
        return httpx.Response(201, json={"data": {"id": "12345", "text": "hi"}})

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    client = LiveXClient(_CREDS, account_user_id="1", timeout=5.0, client=http_client)
    result = await client.post_tweet("hi")
    assert result.posted is True
    assert result.tweet_id == "12345"
    await http_client.aclose()


@pytest.mark.asyncio
async def test_live_client_post_tweet_http_error_is_graceful() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    client = LiveXClient(_CREDS, account_user_id="1", timeout=5.0, client=http_client)
    result = await client.post_tweet("hi")
    assert result.posted is False
    assert "401" in result.detail
    await http_client.aclose()


@pytest.mark.asyncio
async def test_live_client_fetch_mentions_parses_public_metrics() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/mentions"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "111",
                            "author_id": "222",
                            "text": "great work @roboco",
                            "public_metrics": {
                                "like_count": 3,
                                "reply_count": 1,
                                "retweet_count": 0,
                            },
                        }
                    ]
                },
            )
        raise AssertionError(f"unexpected path {request.url.path}")

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    client = LiveXClient(_CREDS, account_user_id="1", timeout=5.0, client=http_client)
    mentions = await client.fetch_mentions(since_id=None, max_results=10)
    assert len(mentions) == 1
    assert mentions[0].id == "111"
    assert mentions[0].like_count == _EXPECTED_LIKE_COUNT
    await http_client.aclose()


@pytest.mark.asyncio
async def test_live_client_resolves_account_id_via_users_me_when_unset() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/users/me"):
            return httpx.Response(200, json={"data": {"id": "999"}})
        return httpx.Response(200, json={"data": []})

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    client = LiveXClient(_CREDS, account_user_id="", timeout=5.0, client=http_client)
    await client.fetch_mentions(since_id=None, max_results=10)
    assert any(p.endswith("/users/me") for p in calls)
    assert any(p.endswith("/users/999/mentions") for p in calls)
    await http_client.aclose()


def test_max_tweet_chars_is_280() -> None:
    expected = 280
    assert expected == MAX_TWEET_CHARS
