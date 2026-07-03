"""Idempotently upserts the single seeded CEO login user at startup.

Exactly one row, ever — no registration route exists. Looked up by primary
key (not email), so changing ROBOCO_CLOUD_AUTH_EMAIL renames the existing
row instead of creating a second one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi_users.password import PasswordHelper
from sqlalchemy import select

from roboco.config import settings
from roboco.db.base import get_session_factory
from roboco.db.tables import UserTable
from roboco.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)
_password_helper = PasswordHelper()


async def ensure_seed_user(db: AsyncSession) -> None:
    """No-op unless cloud_auth_enabled and both email/password are set.

    Does not commit — the caller controls the transaction (see
    ``ensure_seed_user_startup`` for the production call site).
    """
    if not settings.cloud_auth_enabled:
        return

    email = settings.cloud_auth_email
    password = settings.cloud_auth_password
    if not email or not password:
        logger.warning(
            "cloud_auth_enabled but ROBOCO_CLOUD_AUTH_EMAIL/PASSWORD unset — "
            "no login user seeded; /api/auth/login will reject every attempt."
        )
        return

    # Ordered by the primary key's text label (not UserTable.id — under the
    # TYPE_CHECKING split, mypy sees that as a bare UUID, not a column
    # expression) so a hypothetical multi-row state is still deterministic.
    existing = (
        await db.execute(select(UserTable).order_by("id").limit(1))
    ).scalar_one_or_none()

    if existing is None:
        db.add(
            UserTable(
                email=email,
                hashed_password=_password_helper.hash(password),
                is_active=True,
                is_superuser=True,
                is_verified=True,
            )
        )
        logger.info("Seeded cloud-auth login user", email=email)
        return

    changed = False
    if existing.email != email:
        existing.email = email
        changed = True

    verified, updated_hash = _password_helper.verify_and_update(
        password, existing.hashed_password
    )
    if not verified:
        existing.hashed_password = _password_helper.hash(password)
        changed = True
    elif updated_hash is not None:
        existing.hashed_password = updated_hash

    if changed:
        logger.info("cloud-auth seed user updated (email/password rotated)")


async def ensure_seed_user_startup() -> None:
    """Lifespan entry point: best-effort, own session + commit.

    A DB hiccup here must not block startup — the fail-loud secret check
    already ran at Settings construction (the one thing that would otherwise
    break silently).
    """
    if not settings.cloud_auth_enabled:
        return
    try:
        async with get_session_factory()() as db:
            await ensure_seed_user(db)
            await db.commit()
    except Exception as e:
        logger.warning("Cloud-auth seed-user upsert failed", error=str(e))
