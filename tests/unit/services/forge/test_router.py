"""ForgeRouter + registry: host-map registration, per-call provider
resolution off RepoRef.host, and URL parsing that stamps the host.
"""

from __future__ import annotations

import pytest
from roboco.exceptions import GitError
from roboco.services.forge import registry
from roboco.services.forge.base import RepoRef
from roboco.services.forge.gitea import GiteaProvider
from roboco.services.forge.github import GitHubProvider
from roboco.services.forge.registry import (
    provider_for,
    register_project_forge,
)
from roboco.services.forge.router import ForgeRouter


@pytest.fixture(autouse=True)
def _clean_host_map() -> None:
    registry._HOST_PROVIDERS.clear()


def test_github_host_needs_no_registration() -> None:
    register_project_forge("https://github.com/acme/widgets.git", "github")
    assert registry._HOST_PROVIDERS == {}
    assert registry.provider_name_for_host("github.com") == "github"


def test_gitea_host_registers_and_resolves() -> None:
    register_project_forge("https://gitea.example.com/acme/widgets.git", "gitea")
    assert registry.provider_name_for_host("gitea.example.com") == "gitea"


def test_router_resolves_provider_from_ref_host() -> None:
    register_project_forge("https://gitea.example.com/acme/widgets.git", "gitea")
    router = ForgeRouter()
    assert isinstance(
        router._provider_for_ref(RepoRef("a", "b")), GitHubProvider
    )
    assert isinstance(
        router._provider_for_ref(RepoRef("a", "b", host="gitea.example.com")),
        GiteaProvider,
    )


def test_router_parse_github_url_unchanged() -> None:
    ref = ForgeRouter().parse_repo_ref("git@github.com:acme/widgets.git")
    assert ref == RepoRef("acme", "widgets")
    assert ref.host is None


def test_router_parse_registered_gitea_url_stamps_host() -> None:
    register_project_forge("https://gitea.example.com/acme/widgets.git", "gitea")
    ref = ForgeRouter().parse_repo_ref("https://gitea.example.com/acme/widgets.git")
    assert ref.host == "gitea.example.com"


def test_router_parse_unregistered_host_fails_loud() -> None:
    with pytest.raises(GitError, match="registered project"):
        ForgeRouter().parse_repo_ref("https://git.internal.example/a/b.git")


def test_provider_for_gitea_project_uses_git_url_host() -> None:
    class _Project:
        git_provider = "gitea"
        git_url = "https://gitea.example.com/acme/widgets.git"

    provider = provider_for(_Project())
    assert isinstance(provider, GiteaProvider)


def test_provider_for_gitlab_still_rejected() -> None:
    class _Project:
        git_provider = "gitlab"
        git_url = "https://gitlab.com/acme/widgets.git"

    with pytest.raises(GitError, match="GitLab"):
        provider_for(_Project())
