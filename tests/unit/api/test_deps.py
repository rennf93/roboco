"""roboco.api.deps coverage — agent identity / context / role gate helpers.

Covers the slug→UUID resolution, header-based agent ID/context dependencies,
HMAC token enforcement, role coercion fallbacks, notification/task
permission factories, and the choreographer + content-actions wiring.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from roboco.api.deps import (
    _auth_required,
    _check_agent_auth_token,
    _coerce_agent_role,
    _coerce_agent_team,
    _resolve_agent_identity,
    _ServiceHolder,
    get_agent_context,
    get_choreographer,
    get_content_actions,
    get_current_agent_id,
    get_current_agent_slug,
    get_optional_agent_id,
    get_orchestrator,
    get_pagination,
    get_permission_service,
    require_notification_permission,
    require_task_action,
    resolve_agent_id,
    set_orchestrator,
)
from roboco.models import AgentRole, Team
from roboco.models.permissions import AgentContext

_HTTP_400 = 400
_HTTP_401 = 401
_HTTP_403 = 403
_HTTP_503 = 503
_LIMIT_HIGH = 200
_LIMIT_NEG = -5
_OFFSET_NEG = -1
_LIMIT_CAP = 100


# ---------------------------------------------------------------------------
# resolve_agent_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_agent_id_returns_uuid_when_found() -> None:
    expected = uuid4()
    fake_db = MagicMock()
    with patch(
        "roboco.api.deps.resolve_agent_uuid",
        new=AsyncMock(return_value=expected),
    ):
        out = await resolve_agent_id("be-dev-1", fake_db)
    assert out == expected


@pytest.mark.asyncio
async def test_resolve_agent_id_raises_when_missing() -> None:
    with (
        patch("roboco.api.deps.resolve_agent_uuid", new=AsyncMock(return_value=None)),
        pytest.raises(HTTPException) as exc,
    ):
        await resolve_agent_id("ghost", MagicMock())
    assert exc.value.status_code == _HTTP_400


# ---------------------------------------------------------------------------
# Service holder & orchestrator setters
# ---------------------------------------------------------------------------


def test_get_permission_service_caches() -> None:
    _ServiceHolder.permission_service = None
    a = get_permission_service()
    b = get_permission_service()
    assert a is b


def test_set_orchestrator_and_get_orchestrator() -> None:
    fake = MagicMock()
    set_orchestrator(fake)
    out = get_orchestrator()
    assert out is fake
    _ServiceHolder.orchestrator = None


def test_get_orchestrator_raises_503_when_unset() -> None:
    _ServiceHolder.orchestrator = None
    with pytest.raises(HTTPException) as exc:
        get_orchestrator()
    assert exc.value.status_code == _HTTP_503


# ---------------------------------------------------------------------------
# get_current_agent_id / get_current_agent_slug
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_current_agent_id_raises_when_header_missing() -> None:
    with pytest.raises(HTTPException) as exc:
        await get_current_agent_id(MagicMock(), MagicMock(), x_agent_id=None)
    assert exc.value.status_code == _HTTP_401


@pytest.mark.asyncio
async def test_get_current_agent_id_returns_uuid() -> None:
    expected = uuid4()
    with patch(
        "roboco.api.deps.resolve_agent_uuid",
        new=AsyncMock(return_value=expected),
    ):
        out = await get_current_agent_id(
            MagicMock(), MagicMock(), x_agent_id="be-dev-1"
        )
    assert out == expected


@pytest.mark.asyncio
async def test_get_current_agent_slug_raises_when_header_missing() -> None:
    with pytest.raises(HTTPException) as exc:
        await get_current_agent_slug(MagicMock(), MagicMock(), x_agent_id=None)
    assert exc.value.status_code == _HTTP_401


@pytest.mark.asyncio
async def test_get_current_agent_slug_returns_header() -> None:
    out = await get_current_agent_slug(MagicMock(), MagicMock(), x_agent_id="be-dev-1")
    assert out == "be-dev-1"


# ---------------------------------------------------------------------------
# get_optional_agent_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_optional_agent_id_returns_none_when_absent() -> None:
    out = await get_optional_agent_id(MagicMock(), x_agent_id=None)
    assert out is None


@pytest.mark.asyncio
async def test_get_optional_agent_id_returns_uuid_when_present() -> None:
    expected = uuid4()
    with patch(
        "roboco.api.deps.resolve_agent_uuid",
        new=AsyncMock(return_value=expected),
    ):
        out = await get_optional_agent_id(MagicMock(), x_agent_id="be-dev-1")
    assert out == expected


@pytest.mark.asyncio
async def test_get_optional_agent_id_returns_none_on_lookup_failure() -> None:
    """When resolve_agent_id raises HTTPException, return None instead."""
    with patch("roboco.api.deps.resolve_agent_uuid", new=AsyncMock(return_value=None)):
        out = await get_optional_agent_id(MagicMock(), x_agent_id="ghost")
    assert out is None


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def test_auth_required_truthy_values(monkeypatch: pytest.MonkeyPatch) -> None:
    for v in ("1", "true", "yes", "TRUE", "Yes"):
        monkeypatch.setenv("ROBOCO_AGENT_AUTH_REQUIRED", v)
        assert _auth_required() is True


def test_auth_required_falsy_values(monkeypatch: pytest.MonkeyPatch) -> None:
    for v in ("0", "no", "false", ""):
        monkeypatch.setenv("ROBOCO_AGENT_AUTH_REQUIRED", v)
        assert _auth_required() is False


def test_check_agent_auth_token_no_token_in_dev_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_REQUIRED", "false")
    # No raise.
    _check_agent_auth_token("a", "developer", "backend", x_agent_token=None)


def test_check_agent_auth_token_missing_when_required_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_REQUIRED", "true")
    with pytest.raises(HTTPException) as exc:
        _check_agent_auth_token("a", "developer", "backend", x_agent_token=None)
    assert exc.value.status_code == _HTTP_401


def test_check_agent_auth_token_invalid_token_raises() -> None:
    """Even in dev, an INVALID token is still rejected."""
    with (
        patch("roboco.api.deps.verify_agent_token", return_value=False),
        pytest.raises(HTTPException) as exc,
    ):
        _check_agent_auth_token("a", "developer", "backend", x_agent_token="bad")
    assert exc.value.status_code == _HTTP_401


def test_check_agent_auth_token_valid_passes() -> None:
    with patch("roboco.api.deps.verify_agent_token", return_value=True):
        _check_agent_auth_token("a", "developer", "backend", x_agent_token="good")


def test_check_agent_auth_token_missing_under_cloud_auth_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cloud_auth on + agent_auth_required off: token still mandatory."""
    from roboco.api import deps as _deps

    monkeypatch.setattr(_deps.settings, "cloud_auth_enabled", True)
    monkeypatch.delenv("ROBOCO_AGENT_AUTH_REQUIRED", raising=False)
    with pytest.raises(HTTPException) as exc:
        _check_agent_auth_token("a", "developer", "backend", x_agent_token=None)
    assert exc.value.status_code == _HTTP_401


def test_check_agent_auth_token_valid_under_cloud_auth_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from roboco.api import deps as _deps

    monkeypatch.setattr(_deps.settings, "cloud_auth_enabled", True)
    with patch("roboco.api.deps.verify_agent_token", return_value=True):
        _check_agent_auth_token("a", "developer", "backend", x_agent_token="good")


def test_check_agent_auth_token_dev_mode_no_token_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cloud_auth off + agent_auth_required off: dev unchanged."""
    from roboco.api import deps as _deps

    monkeypatch.setattr(_deps.settings, "cloud_auth_enabled", False)
    monkeypatch.delenv("ROBOCO_AGENT_AUTH_REQUIRED", raising=False)
    _check_agent_auth_token("a", "developer", "backend", x_agent_token=None)


def test_check_agent_auth_token_agent_auth_required_no_token_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cloud_auth off + agent_auth_required on: existing behavior unchanged."""
    from roboco.api import deps as _deps

    monkeypatch.setattr(_deps.settings, "cloud_auth_enabled", False)
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_REQUIRED", "true")
    with pytest.raises(HTTPException) as exc:
        _check_agent_auth_token("a", "developer", "backend", x_agent_token=None)
    assert exc.value.status_code == _HTTP_401


# ---------------------------------------------------------------------------
# _resolve_agent_identity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_agent_identity_system_role() -> None:
    aid = uuid4()
    out_id, out_slug = await _resolve_agent_identity(MagicMock(), str(aid), "system")
    assert out_id == aid
    assert out_slug == "system"


@pytest.mark.asyncio
async def test_resolve_agent_identity_system_invalid_uuid_raises() -> None:
    with pytest.raises(HTTPException) as exc:
        await _resolve_agent_identity(MagicMock(), "not-a-uuid", "system")
    assert exc.value.status_code == _HTTP_400


@pytest.mark.asyncio
async def test_resolve_agent_identity_normal_path() -> None:
    expected = (uuid4(), "be-dev-1")
    with patch(
        "roboco.api.deps.resolve_agent_identity",
        new=AsyncMock(return_value=expected),
    ):
        out = await _resolve_agent_identity(MagicMock(), "be-dev-1", "developer")
    assert out == expected


@pytest.mark.asyncio
async def test_resolve_agent_identity_normal_path_not_found_raises() -> None:
    with (
        patch(
            "roboco.api.deps.resolve_agent_identity",
            new=AsyncMock(return_value=None),
        ),
        pytest.raises(HTTPException) as exc,
    ):
        await _resolve_agent_identity(MagicMock(), "ghost", "developer")
    assert exc.value.status_code == _HTTP_400


# ---------------------------------------------------------------------------
# _coerce_agent_role
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coerce_agent_role_valid_enum_value() -> None:
    out = await _coerce_agent_role(MagicMock(), "developer", uuid4(), "be-dev-1")
    assert out == AgentRole.DEVELOPER


@pytest.mark.asyncio
async def test_coerce_agent_role_falls_back_to_db_when_invalid() -> None:
    """Slug-as-role like 'main-pm' coerces via DB lookup."""
    fake_db = MagicMock()
    fake_result = MagicMock()
    fake_result.scalar_one_or_none.return_value = AgentRole.MAIN_PM
    fake_db.execute = AsyncMock(return_value=fake_result)

    out = await _coerce_agent_role(fake_db, "main-pm", uuid4(), "main-pm")
    assert out == AgentRole.MAIN_PM


@pytest.mark.asyncio
async def test_coerce_agent_role_no_db_record_raises() -> None:
    fake_db = MagicMock()
    fake_result = MagicMock()
    fake_result.scalar_one_or_none.return_value = None
    fake_db.execute = AsyncMock(return_value=fake_result)

    with pytest.raises(HTTPException) as exc:
        await _coerce_agent_role(fake_db, "garbage", uuid4(), "be-dev-1")
    assert exc.value.status_code == _HTTP_400


# ---------------------------------------------------------------------------
# _coerce_agent_team
# ---------------------------------------------------------------------------


def test_coerce_agent_team_valid() -> None:
    assert _coerce_agent_team("backend") == Team.BACKEND


def test_coerce_agent_team_uppercase() -> None:
    assert _coerce_agent_team("BACKEND") == Team.BACKEND


def test_coerce_agent_team_none_returns_none() -> None:
    assert _coerce_agent_team(None) is None


def test_coerce_agent_team_invalid_returns_none() -> None:
    assert _coerce_agent_team("not-a-team") is None


def test_coerce_agent_team_empty_returns_none() -> None:
    assert _coerce_agent_team("") is None


# ---------------------------------------------------------------------------
# get_agent_context
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_agent_context_missing_id_raises() -> None:
    with pytest.raises(HTTPException) as exc:
        await get_agent_context(
            MagicMock(),
            MagicMock(),
            x_agent_id=None,
            x_agent_role="developer",
        )
    assert exc.value.status_code == _HTTP_401


@pytest.mark.asyncio
async def test_get_agent_context_missing_role_raises() -> None:
    with pytest.raises(HTTPException) as exc:
        await get_agent_context(
            MagicMock(),
            MagicMock(),
            x_agent_id="be-dev-1",
            x_agent_role=None,
        )
    assert exc.value.status_code == _HTTP_401


@pytest.mark.asyncio
async def test_get_agent_context_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_REQUIRED", "false")
    aid = uuid4()
    with patch(
        "roboco.api.deps.resolve_agent_identity",
        new=AsyncMock(return_value=(aid, "be-dev-1")),
    ):
        ctx = await get_agent_context(
            MagicMock(),
            MagicMock(),
            x_agent_id="be-dev-1",
            x_agent_role="developer",
            x_agent_team="backend",
        )
    assert ctx.agent_id == aid
    assert ctx.role == AgentRole.DEVELOPER
    assert ctx.team == Team.BACKEND
    assert ctx.slug == "be-dev-1"


# ---------------------------------------------------------------------------
# Permission factories — exercise the inner closure on both pass and fail.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_require_notification_permission_passes_when_allowed() -> None:
    fake_perms = MagicMock()
    fake_perms.can_send_notifications = MagicMock(return_value=True)
    agent = AgentContext(agent_id=uuid4(), role=AgentRole.MAIN_PM)
    dep = require_notification_permission()
    await dep(agent, fake_perms)


@pytest.mark.asyncio
async def test_require_notification_permission_raises_when_denied() -> None:
    fake_perms = MagicMock()
    fake_perms.can_send_notifications = MagicMock(return_value=False)
    agent = AgentContext(agent_id=uuid4(), role=AgentRole.DEVELOPER, team=Team.BACKEND)
    dep = require_notification_permission()
    with pytest.raises(HTTPException) as exc:
        await dep(agent, fake_perms)
    assert exc.value.status_code == _HTTP_403


@pytest.mark.asyncio
async def test_require_task_action_passes_when_allowed() -> None:
    fake_perms = MagicMock()
    fake_perms.can_perform_task_action = MagicMock(return_value=True)
    agent = AgentContext(agent_id=uuid4(), role=AgentRole.MAIN_PM)
    dep = require_task_action("create")
    await dep(agent, fake_perms)


@pytest.mark.asyncio
async def test_require_task_action_raises_when_denied() -> None:
    fake_perms = MagicMock()
    fake_perms.can_perform_task_action = MagicMock(return_value=False)
    agent = AgentContext(agent_id=uuid4(), role=AgentRole.DEVELOPER, team=Team.BACKEND)
    dep = require_task_action("close")
    with pytest.raises(HTTPException) as exc:
        await dep(agent, fake_perms)
    assert exc.value.status_code == _HTTP_403


# ---------------------------------------------------------------------------
# Choreographer / ContentActions wiring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_choreographer_returns_instance() -> None:
    fake_db = MagicMock()
    chor = await get_choreographer(fake_db)
    assert chor is not None


@pytest.mark.asyncio
async def test_get_content_actions_returns_instance() -> None:
    fake_db = MagicMock()
    ca = await get_content_actions(fake_db)
    assert ca is not None


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


def test_pagination_defaults() -> None:
    p = get_pagination()
    assert p.limit > 0
    assert p.offset == 0


def test_pagination_clamps_high_limit() -> None:
    p = get_pagination(limit=_LIMIT_HIGH)
    # Limit is capped at the documented maximum.
    assert p.limit <= _LIMIT_CAP


def test_pagination_clamps_negative_limit() -> None:
    p = get_pagination(limit=_LIMIT_NEG)
    assert p.limit >= 1


def test_pagination_clamps_negative_offset() -> None:
    p = get_pagination(offset=_OFFSET_NEG)
    assert p.offset >= 0
