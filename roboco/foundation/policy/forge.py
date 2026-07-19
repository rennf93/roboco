"""Forge (git host) provider detection + registration-time validation.

Phase 0 of the forge-providers spec (``docs/internal/specs/2026-07-18-forge-
providers-spec.md``): RoboCo's PR/CI/review surface is GitHub-only today
(``GitService`` is inline ``httpx`` hardcoded to ``github.com``). Pointing a
project at a GitLab/Gitea ``git_url`` used to fail silently, several steps deep,
at first PR — this module turns that into a loud, registration-time rejection
naming exactly what's unsupported and why, with an escape hatch for GitHub
Enterprise (a github.com-shaped API on a different host).

Pure + DB-free so it is unit-testable; ``ProjectService.create``/``update`` is
the sole enforcement chokepoint (see ``roboco/services/project.py``).
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

KNOWN_PROVIDERS: tuple[str, ...] = ("github", "gitlab", "gitea")

# scp-like SSH syntax: [user@]host:path (e.g. git@github.com:owner/repo.git).
# Only matches when the URL carries no "://" scheme (checked by the caller).
_SCP_HOST_RE = re.compile(r"^(?:[^@/]+@)?(?P<host>[^/:]+):")


def extract_host(git_url: str) -> str | None:
    """Pull the host out of an https, ssh://, or scp-like git URL.

    Returns None when no host can be found (unparseable input). Public —
    the forge registry keys its host→provider routing map on this.
    """
    url = git_url.strip()
    if not url:
        return None
    if "://" in url:
        host = urlsplit(url).hostname
        return host.lower() if host else None
    match = _SCP_HOST_RE.match(url)
    if match:
        return match.group("host").lower()
    return None


def detect_provider(git_url: str) -> str | None:
    """Best-effort provider from the ``git_url`` host alone.

    Only the two SaaS hosts are auto-detected (``github.com`` -> "github",
    ``gitlab.com`` -> "gitlab"); every other host — including a self-hosted
    GitLab/Gitea/GHE instance — returns None, since the host alone can't tell
    those apart. A project on a non-SaaS host must set ``git_provider``
    explicitly (a one-click choice in the panel's project dialog).
    """
    host = extract_host(git_url)
    if host == "github.com":
        return "github"
    if host == "gitlab.com":
        return "gitlab"
    return None


def validate_project_forge(git_url: str | None, git_provider: str | None) -> str | None:
    """Registration-time forge validation. Returns an error message, or None.

    Rules, in order:

    - empty/None ``git_url`` -> OK (no repo to validate yet).
    - an unknown ``git_provider`` string -> error naming ``KNOWN_PROVIDERS``.
    - explicit ``git_provider="github"`` -> OK regardless of host (the GitHub
      Enterprise escape hatch — current behavior preserved).
    - explicit ``git_provider="gitea"`` -> OK (Phase 2: the Gitea transport
      is live; the host comes from the git_url).
    - explicit ``git_provider="gitlab"`` -> error: recognized but not yet
      supported.
    - no explicit ``git_provider``, host detects to "github" -> OK.
    - no explicit ``git_provider``, anything else (unknown host, or a
      detected but unsupported host like gitlab.com) -> error steering the
      operator to an explicit provider choice.
    """
    if not git_url:
        return None

    if git_provider is not None:
        if git_provider not in KNOWN_PROVIDERS:
            return (
                f"Unknown git_provider {git_provider!r}; must be one of "
                f"{', '.join(KNOWN_PROVIDERS)}."
            )
        if git_provider in ("github", "gitea", "gitlab"):
            return None
        return f"git_provider={git_provider!r} is recognized but not yet supported."

    if detect_provider(git_url) in ("github", "gitlab"):
        return None
    return (
        "RoboCo supports GitHub- and GitLab-hosted repos by default. For a "
        'self-hosted forge set git_provider explicitly ("gitea", "gitlab", '
        'or "github" for GitHub Enterprise).'
    )
