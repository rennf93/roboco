"""Coverage for roboco.api.schemas.channels.require_channel_admin."""

from __future__ import annotations

from http import HTTPStatus
from uuid import uuid4

import pytest
from fastapi import HTTPException
from roboco.api.schemas.channels import require_channel_admin
from roboco.models import AgentRole, Team
from roboco.models.permissions import AgentContext


def test_require_channel_admin_allows_ceo() -> None:
    ctx = AgentContext(agent_id=uuid4(), role=AgentRole.CEO)
    require_channel_admin(ctx)  # No raise.


def test_require_channel_admin_denies_developer() -> None:
    """Line 84: developer raises HTTPException(403)."""
    ctx = AgentContext(agent_id=uuid4(), role=AgentRole.DEVELOPER, team=Team.BACKEND)
    with pytest.raises(HTTPException) as exc:
        require_channel_admin(ctx)
    assert exc.value.status_code == HTTPStatus.FORBIDDEN
