"""Human-readable formatting for notification subjects/bodies.

Producers used to interpolate raw task UUIDs and agent UUIDs/role-literals
straight into text a human reads (panel list, bell dropdown, Telegram DM —
``notification_delivery.py`` reuses ``subject`` verbatim for Telegram). These
two helpers give every producer one place to render a task as its title
(falling back to a short id) and an agent as its slug (falling back to the
raw value), instead of re-deriving it ad hoc per call site.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from roboco.foundation.identity import AGENTS

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_TITLE_MAX = 40
# Built once — the fixed 26-agent roster never changes at runtime.
_UUID_TO_SLUG: dict[str, str] = {str(row.uuid): slug for slug, row in AGENTS.items()}


def task_display(task: Any | None, task_id: str | UUID) -> str:
    """ "'<title>' (#<id8>)" when a title is available, else "#<id8>".

    ``task`` may be a task row (``.title`` attribute), a bare title string,
    or ``None`` — every call site already holds one of those, so this never
    triggers a fresh DB fetch just to render text.
    """
    id8 = str(task_id)[:8]
    title = task if isinstance(task, str) else getattr(task, "title", None)
    return f"'{title[:_TITLE_MAX]}' (#{id8})" if title else f"#{id8}"


async def agent_display(
    value: str | UUID | None, db: AsyncSession | None = None
) -> str | None:
    """Slug for an agent UUID/slug, or the raw value if unresolvable.

    ``None`` passes through unchanged — callers keep their own "unassigned"
    / "its owner" wording. The static reverse map (built above) is checked
    first with no I/O at all; a DB lookup only fires when a session is
    supplied AND the value parses as a UUID the static map doesn't have
    (e.g. a freshly-seeded row), failing open to the raw string on a miss.
    """
    if value is None:
        return None
    key = str(value)
    slug = _UUID_TO_SLUG.get(key)
    if slug:
        return slug
    if db is not None:
        try:
            resolved_uuid = UUID(key)
        except ValueError:
            return key
        from roboco.services.repositories.query_helpers import get_agent_slug

        found = await get_agent_slug(db, resolved_uuid)
        if found:
            return found
    return key
