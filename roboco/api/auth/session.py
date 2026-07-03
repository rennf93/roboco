"""Shared session-cookie resolution.

One implementation, used by both the HTTP dual-path
(``roboco.api.deps.get_agent_context``) and the WS panel-token gate
(``roboco.api.websocket._require_panel_token``), so cookie validation can't
drift between the two call sites.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase

from roboco.api.auth.backend import get_jwt_strategy
from roboco.api.auth.manager import UserManager
from roboco.db.tables import UserTable

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def resolve_session_user(token: str | None, db: AsyncSession) -> UserTable | None:
    """Validate a cloud-auth session cookie; return the CEO user, or None."""
    if not token:
        return None
    manager = UserManager(SQLAlchemyUserDatabase(db, UserTable))
    return await get_jwt_strategy().read_token(token, manager)
