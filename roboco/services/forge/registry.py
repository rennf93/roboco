"""Provider resolution — wiring only, no transport logic of its own.

Maps a project's ``git_provider`` (Phase 0's ``projects.git_provider`` column;
``roboco/foundation/policy/forge.py`` already rejects ``gitlab``/``gitea`` at
registration time) onto a concrete :class:`GitProvider`. Phase 1 only ever
resolves to :class:`GitHubProvider` in practice — the other branches exist so
resolution fails loud instead of silently misbehaving if that invariant is
ever bypassed (a row written directly, a future migration path, ...).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from roboco.exceptions import GitError
from roboco.services.forge.github import GitHubProvider

if TYPE_CHECKING:
    from roboco.services.forge.base import GitProvider


def provider_for(project: Any | None = None) -> GitProvider:
    """Resolve the :class:`GitProvider` for a project (or the system default).

    ``project`` is duck-typed (only ``.git_provider`` is read) so this module
    never needs to import a concrete Project model. ``None`` — no project in
    scope, or a project whose ``git_provider`` is unset — resolves to GitHub,
    matching ``roboco.foundation.policy.forge.detect_provider``'s default.
    """
    provider_name = (
        getattr(project, "git_provider", None) if project is not None else None
    )
    if provider_name in (None, "github"):
        return GitHubProvider()
    raise GitError(
        f"Unsupported git_provider {provider_name!r} — GitLab/Gitea support "
        "is not implemented yet.",
        {"git_provider": provider_name},
    )
