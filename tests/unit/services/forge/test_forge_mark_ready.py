"""Taking a draft PR out of draft: GitHub over GraphQL (its REST API cannot),
every other forge a shaped 501."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.config import settings
from roboco.services.forge import GiteaProvider, GitHubProvider, RepoRef
from roboco.services.forge.gitlab import GitLabProvider

_REF = RepoRef("acme", "app")
_NOT_IMPLEMENTED = 501


def _client() -> Any:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock(return_value=MagicMock(status_code=200))
    return client


@pytest.mark.parametrize(
    ("api_base", "graphql"),
    [
        ("https://api.github.com", "https://api.github.com/graphql"),
        ("https://ghe.acme.test/api/v3", "https://ghe.acme.test/api/graphql"),
    ],
)
async def test_github_un_drafts_through_graphql(
    monkeypatch: pytest.MonkeyPatch, api_base: str, graphql: str
) -> None:
    monkeypatch.setattr(settings, "github_api_base_url", api_base)
    client = _client()
    with patch("roboco.services.forge.github.httpx.AsyncClient", return_value=client):
        await GitHubProvider().mark_pr_ready_for_review(_REF, "SECRET", "PR_kw123")

    call = client.post.await_args
    assert call.args[0] == graphql
    assert call.kwargs["headers"]["Authorization"] == "Bearer SECRET"
    body = call.kwargs["json"]
    assert "markPullRequestReadyForReview" in body["query"]
    assert body["variables"] == {"id": "PR_kw123"}


@pytest.mark.parametrize(
    "provider", [GiteaProvider("gitea.acme.test"), GitLabProvider("gitlab.acme.test")]
)
async def test_other_forges_answer_a_shaped_501(provider: Any) -> None:
    resp = await provider.mark_pr_ready_for_review(_REF, "SECRET", "PR_kw123")
    assert resp.status_code == _NOT_IMPLEMENTED
    assert "not supported" in resp.json()["message"]
