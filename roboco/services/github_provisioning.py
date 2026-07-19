"""Repository provisioning — create new repos in a dedicated org/group.

This is the ONE place that *creates* forge repositories; everywhere else the
system only clones/branches/PRs repos that already exist. Used by the pitch
approval flow to auto-provision a repo per target cell.

GitHub is the default and only forge that needs no extra config (Phase 1).
Phase 4 adds GitLab/Gitea parity: ``ROBOCO_PROVISIONING_PROVIDER`` selects the
forge and, for a self-hosted GitLab/Gitea instance, ``ROBOCO_PROVISIONING_HOST``
names it — the class/module names stay GitHub-flavored for backward
compatibility (``pitch.py`` and existing imports read them unchanged), but the
service now dispatches to whichever :class:`~roboco.services.forge.base.GitProvider`
is configured.

The provisioning token + org/group live only in server-side config and are
never injected into an agent container. When unconfigured the service reports
``enabled = False`` and ``create_repo`` raises ``ProvisioningDisabledError`` —
so on a default deployment the whole pitch→provision path is inert and nothing
is created until the CEO sets the token. That keeps the capability additive.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from roboco.config import settings
from roboco.services.forge.base import RepoRef
from roboco.services.forge.gitea import GiteaProvider
from roboco.services.forge.github import GitHubProvider
from roboco.services.forge.gitlab import GitLabProvider


class ProvisioningError(Exception):
    """Repository provisioning failed (network error or non-2xx response)."""


class ProvisioningDisabledError(ProvisioningError):
    """Provisioning was requested but is not configured (no token/org)."""


@dataclass(frozen=True)
class ProvisionedRepo:
    """The pieces of a freshly-created repo we need downstream."""

    full_name: str
    clone_url: str
    html_url: str


# A duplicate repo/project create is treated idempotently (fetch + reuse
# instead of erroring — #83/#84): GitHub replies 422 "name already exists",
# Gitea 409/422 "already exists", GitLab 400→(reshaped)422 "has already been
# taken". Matched on status + either phrase, case-insensitively.
_ALREADY_EXISTS_STATUSES = frozenset({409, 422})
_ALREADY_EXISTS_PHRASES = ("already exists", "has already been taken")


def _is_already_exists(resp: Any) -> bool:
    if resp.status_code not in _ALREADY_EXISTS_STATUSES:
        return False
    text = (resp.text or "").lower()
    return any(phrase in text for phrase in _ALREADY_EXISTS_PHRASES)


def _build_provider(
    provider_name: str, *, base_url: str, host: str
) -> GitHubProvider | GitLabProvider | GiteaProvider:
    # Union, not the ``GitProvider`` ABC: create_org_repo/get_repo take
    # client=/timeout= kwargs the ABC doesn't declare (git.py's own forge
    # calls never need them), and every concrete provider here does.
    if provider_name == "gitlab":
        return GitLabProvider(host)
    if provider_name == "gitea":
        return GiteaProvider(host)
    return GitHubProvider(base_url=base_url)


class GitHubProvisioningService:
    """Create private repos in the configured org/group via the forge's REST API.

    Despite the name (kept for backward compatibility), the target forge is
    provider-aware: ``ROBOCO_PROVISIONING_PROVIDER`` picks github (default) /
    gitlab / gitea, dispatching to the matching ``GitProvider``.
    """

    def __init__(
        self,
        *,
        token: str | None = None,
        org: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        client: httpx.AsyncClient | None = None,
        provider_name: str | None = None,
        host: str | None = None,
    ) -> None:
        self._token = token if token is not None else settings.provisioning_token
        self._org = org if org is not None else settings.provisioning_org
        self._base_url = (base_url or settings.github_api_base_url).rstrip("/")
        self._timeout = (
            timeout if timeout is not None else settings.provisioning_timeout_seconds
        )
        self._client = client
        self._owns_client = client is None
        self._provider_name = (
            (
                provider_name
                if provider_name is not None
                else settings.provisioning_provider
            )
            .strip()
            .lower()
        )
        self._host = (host if host is not None else settings.provisioning_host) or ""
        self._provider = _build_provider(
            self._provider_name, base_url=self._base_url, host=self._host
        )

    @property
    def enabled(self) -> bool:
        """True only when the master switch + token + org are configured —
        a self-hosted target (gitlab/gitea) additionally needs the instance
        host set."""
        base_ok = bool(settings.provisioning_enabled and self._token and self._org)
        if self._provider_name in ("gitlab", "gitea"):
            return base_ok and bool(self._host)
        return base_ok

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def create_repo(
        self, name: str, description: str = "", *, private: bool = True
    ) -> ProvisionedRepo:
        """Create ``org/name`` (auto-initialised so it is immediately cloneable).

        Idempotent by name: if a prior partially-rolled-back approval left
        ``org/name`` on the forge (the DB transaction rolled back but the repo
        did not), the forge replies with an already-exists status (see
        :func:`_is_already_exists`). Instead of erroring and orphaning the
        re-approval, fetch and return the existing repo so the caller reuses
        its ``clone_url`` to (re)register the Project (#83/#84).
        """
        if not self.enabled:
            raise ProvisioningDisabledError(self._disabled_message())
        client = await self._http()
        try:
            resp = await self._provider.create_org_repo(
                self._token,
                self._org,
                name=name,
                description=description[:350],
                private=private,
                auto_init=True,
                client=client,
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            msg = f"Repo creation failed for '{name}': {exc}"
            raise ProvisioningError(msg) from exc
        if _is_already_exists(resp):
            # The repo is already on the forge from a rolled-back prior
            # attempt — reuse it instead of orphaning the re-approval.
            return await self._fetch_existing_repo(name)
        if not resp.is_success:
            detail = resp.text[:200] if resp.text else "no body"
            msg = f"Repo creation failed for '{name}' ({resp.status_code}): {detail}"
            raise ProvisioningError(msg)
        body = resp.json()
        return ProvisionedRepo(
            full_name=str(body.get("full_name", f"{self._org}/{name}")),
            clone_url=str(body.get("clone_url", "")),
            html_url=str(body.get("html_url", "")),
        )

    def _disabled_message(self) -> str:
        base = (
            f"{self._provider_name.capitalize()} provisioning is not configured. "
            "Set ROBOCO_PROVISIONING_TOKEN and ROBOCO_PROVISIONING_ORG"
        )
        if self._provider_name in ("gitlab", "gitea"):
            return f"{base} and ROBOCO_PROVISIONING_HOST."
        return f"{base}."

    def _existing_repo_ref(self, name: str) -> RepoRef:
        """The repo's identity for a ``get_repo`` re-fetch. GitLab addresses
        a project by its FULL namespace path (``org/name``) packed into
        ``RepoRef.owner``; GitHub/Gitea use the plain ``owner, repo`` pair."""
        if self._provider_name == "gitlab":
            return RepoRef(f"{self._org}/{name}", "", host=self._host or None)
        return RepoRef(self._org, name, host=self._host or None)

    async def _fetch_existing_repo(self, name: str) -> ProvisionedRepo:
        """GET ``org/name`` and rebuild a ProvisionedRepo (idempotent re-create)."""
        client = await self._http()
        try:
            resp = await self._provider.get_repo(
                self._existing_repo_ref(name),
                self._token,
                client=client,
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            msg = f"Repo fetch failed for '{name}': {exc}"
            raise ProvisioningError(msg) from exc
        if not resp.is_success:
            detail = resp.text[:200] if resp.text else "no body"
            msg = f"Repo fetch failed for '{name}' ({resp.status_code}): {detail}"
            raise ProvisioningError(msg)
        body = resp.json()
        return ProvisionedRepo(
            full_name=str(body.get("full_name", f"{self._org}/{name}")),
            clone_url=str(body.get("clone_url", "")),
            html_url=str(body.get("html_url", "")),
        )


def get_github_provisioning_service(
    client: httpx.AsyncClient | None = None,
) -> GitHubProvisioningService:
    """Build a GitHubProvisioningService from current settings."""
    return GitHubProvisioningService(client=client)
