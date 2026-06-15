"""GitHub repository provisioning — create new repos in a dedicated org.

This is the ONE place that *creates* GitHub repositories; everywhere else the
system only clones/branches/PRs repos that already exist. Used by the pitch
approval flow to auto-provision a repo per target cell.

The provisioning token + org live only in server-side config and are never
injected into an agent container. When unconfigured the service reports
``enabled = False`` and ``create_repo`` raises ``ProvisioningDisabledError`` —
so on a default deployment the whole pitch→provision path is inert and nothing
is created until the CEO sets the token. That keeps the capability additive.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from roboco.config import settings


class ProvisioningError(Exception):
    """Repository provisioning failed (network error or non-2xx response)."""


class ProvisioningDisabledError(ProvisioningError):
    """Provisioning was requested but is not configured (no token/org)."""


@dataclass(frozen=True)
class ProvisionedRepo:
    """The pieces of a freshly-created GitHub repo we need downstream."""

    full_name: str
    clone_url: str
    html_url: str


class GitHubProvisioningService:
    """Create private repos in the configured org via the GitHub REST API."""

    def __init__(
        self,
        *,
        token: str | None = None,
        org: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token = token if token is not None else settings.provisioning_token
        self._org = org if org is not None else settings.provisioning_org
        self._base_url = (base_url or settings.github_api_base_url).rstrip("/")
        self._timeout = (
            timeout if timeout is not None else settings.provisioning_timeout_seconds
        )
        self._client = client
        self._owns_client = client is None

    @property
    def enabled(self) -> bool:
        """True only when the master switch + token + org are all configured."""
        return bool(settings.provisioning_enabled and self._token and self._org)

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
        """Create ``org/name`` (auto-initialised so it is immediately cloneable)."""
        if not self.enabled:
            msg = (
                "GitHub provisioning is not configured. Set "
                "ROBOCO_PROVISIONING_TOKEN and ROBOCO_PROVISIONING_ORG."
            )
            raise ProvisioningDisabledError(msg)
        client = await self._http()
        try:
            resp = await client.post(
                f"{self._base_url}/orgs/{self._org}/repos",
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json={
                    "name": name,
                    "description": description[:350],
                    "private": private,
                    "auto_init": True,
                },
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            msg = f"GitHub repo creation failed for '{name}': {exc}"
            raise ProvisioningError(msg) from exc
        if not resp.is_success:
            detail = resp.text[:200] if resp.text else "no body"
            msg = (
                f"GitHub repo creation failed for '{name}' "
                f"({resp.status_code}): {detail}"
            )
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
