"""UserManager + the get_user_db/get_user_manager dependency chain.

Imports roboco.db.base directly (not roboco.api.deps.DbSession) so this
package never depends on the deps module — deps depends on auth, not the
other way around.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated
from uuid import UUID

from fastapi import Depends
from fastapi_users import BaseUserManager, UUIDIDMixin
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase

from roboco.config import settings
from roboco.db.base import get_db
from roboco.db.tables import UserTable

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession

# Placeholder used only while cloud auth is off — BaseUserManager requires a
# non-empty secret attribute, but the reset-password/verify flows it signs
# are non-goals (never mounted), so the value is inert unless those routes
# are wired up in a future change.
_UNUSED_SECRET = "cloud-auth-off"


class UserManager(UUIDIDMixin, BaseUserManager[UserTable, UUID]):
    """The single-user manager backing cloud auth's CEO login."""

    def __init__(self, user_db: SQLAlchemyUserDatabase[UserTable, UUID]) -> None:
        secret = settings.cloud_auth_secret or _UNUSED_SECRET
        self.reset_password_token_secret = secret
        self.verification_token_secret = secret
        super().__init__(user_db)


async def get_user_db(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SQLAlchemyUserDatabase[UserTable, UUID]:
    """FastAPI Users' SQLAlchemy adapter over the shared request session."""
    return SQLAlchemyUserDatabase(db, UserTable)


async def get_user_manager(
    user_db: Annotated[SQLAlchemyUserDatabase[UserTable, UUID], Depends(get_user_db)],
) -> AsyncGenerator[UserManager]:
    yield UserManager(user_db)
