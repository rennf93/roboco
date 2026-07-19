"""Provider resolution + the host↔provider map behind per-call routing.

``provider_for(project)`` maps a project's ``git_provider`` (Phase 0's
``projects.git_provider`` column) onto a concrete :class:`GitProvider`.
Phase 2 adds gitea: the provider is addressed by the instance host, derived
from the project's ``git_url``.

The module also keeps the process-wide **host map** the
:class:`~roboco.services.forge.router.ForgeRouter` consults per call:
``register_project_forge`` is invoked at the project chokepoints every git
flow already crosses (``ProjectService.create``/``update`` and the decrypted
token reads), so by the time any REST call happens for a gitea project its
host is registered. In-memory and per-process by design — a restart forgets
it and the very next project/token read re-registers (the same self-healing
posture as the read-clone sync throttle).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from roboco.exceptions import GitError
from roboco.foundation.policy.forge import extract_host
from roboco.services.forge.gitea import GiteaProvider
from roboco.services.forge.github import GitHubProvider

if TYPE_CHECKING:
    from roboco.services.forge.base import GitProvider

# host (lowercase) → provider name ("github" | "gitea"). github.com is
# implicit and never needs registering. _HOST_SCHEMES remembers a plain-http
# host (LAN instance with no TLS terminator) so the API base matches the
# git_url's own scheme; absent = https.
_HOST_PROVIDERS: dict[str, str] = {}
_HOST_SCHEMES: dict[str, str] = {}


def host_of(git_url: str | None) -> str | None:
    """The forge host of a git URL (https/ssh/scp forms), or None."""
    if not git_url:
        return None
    return extract_host(git_url)


def _scheme_of(git_url: str) -> str:
    return "http" if git_url.strip().lower().startswith("http://") else "https"


def register_project_forge(git_url: str | None, git_provider: str | None) -> None:
    """Record a project's host→provider mapping for per-call routing.

    Called from the ProjectService chokepoints; a github.com host or a
    missing provider records nothing (GitHub is the router's default).
    """
    host = host_of(git_url)
    if host is None or host == "github.com" or git_url is None:
        return
    if git_provider in ("github", "gitea"):
        _HOST_PROVIDERS[host] = git_provider
        _HOST_SCHEMES[host] = _scheme_of(git_url)


def provider_name_for_host(host: str) -> str | None:
    """The registered provider name for a host — None when unregistered."""
    if host == "github.com":
        return "github"
    return _HOST_PROVIDERS.get(host.lower())


def scheme_for_host(host: str) -> str:
    """The registered scheme for a host — https unless the project's git_url
    said otherwise."""
    return _HOST_SCHEMES.get(host.lower(), "https")


def provider_for(project: Any | None = None) -> GitProvider:
    """Resolve the :class:`GitProvider` for a project (or the system default).

    ``project`` is duck-typed (only ``.git_provider`` / ``.git_url`` are
    read) so this module never needs to import a concrete Project model.
    ``None`` — no project in scope, or a project whose ``git_provider`` is
    unset — resolves to GitHub, matching
    ``roboco.foundation.policy.forge.detect_provider``'s default.
    """
    provider_name = (
        getattr(project, "git_provider", None) if project is not None else None
    )
    if provider_name in (None, "github"):
        return GitHubProvider()
    if provider_name == "gitea":
        host = host_of(getattr(project, "git_url", None))
        if host is None:
            raise GitError(
                "gitea project has no parseable git_url host",
                {"git_provider": provider_name},
            )
        return GiteaProvider(host, scheme=scheme_for_host(host))
    raise GitError(
        f"Unsupported git_provider {provider_name!r} — GitLab support "
        "is not implemented yet.",
        {"git_provider": provider_name},
    )
