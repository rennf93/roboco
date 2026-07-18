"""Git-forge provider seam (Phase 1 of the forge-providers spec).

``roboco/services/git.py`` used to interleave local-git subprocess work with
inline ``httpx`` calls against GitHub's REST API. This package pulls the REST
transport out from under it behind a provider-agnostic contract
(:mod:`roboco.services.forge.base`) so a future GitLab/Gitea adapter is a new
module here, not a rewrite of ``GitService``.

``base`` is pure contracts, ``github`` is the GitHub REST transport, and
``registry`` resolves a project onto its provider. Nothing here imports
``roboco.services.*`` — ``GitService`` depends on this package, never the
reverse.
"""

from __future__ import annotations

from roboco.services.forge.base import GitProvider, RepoRef
from roboco.services.forge.github import GitHubProvider
from roboco.services.forge.registry import provider_for

__all__ = ["GitHubProvider", "GitProvider", "RepoRef", "provider_for"]
