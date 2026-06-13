"""Provider API route coverage — async httpx client + dependency overrides."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.provider import router as provider_router
from roboco.db.tables import ModelAssignmentTable, ProviderConfigTable
from roboco.models import AgentRole, Team
from roboco.models.base import ModelProvider
from roboco.models.permissions import AgentContext
from sqlalchemy import delete, select

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


def _make_app(db_session, role: AgentRole = AgentRole.MAIN_PM, team=None) -> FastAPI:
    app = FastAPI()
    app.include_router(provider_router, prefix="/api/providers")

    async def _override_db():
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(agent_id=uuid4(), role=role, team=team)

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent
    return app


@pytest_asyncio.fixture
async def app_client(
    db_session: AsyncSession,
) -> AsyncIterator[AsyncClient]:
    app = _make_app(db_session)
    suffix = uuid4().hex[:8]
    # Only seed if not already present (set_ollama_api_key in a prior test
    # may have committed rows that survive rollback isolation).
    existing = (
        await db_session.execute(
            select(ProviderConfigTable).where(
                ProviderConfigTable.type == ModelProvider.OLLAMA_CLOUD
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db_session.add(
            ProviderConfigTable(
                name=f"anthropic-test-{suffix}",
                type=ModelProvider.ANTHROPIC,
                enabled=True,
            )
        )
        db_session.add(
            ProviderConfigTable(
                name=f"ollama-test-{suffix}",
                type=ModelProvider.OLLAMA_CLOUD,
                enabled=False,
                base_url="https://ollama.example.com",
            )
        )
        await db_session.flush()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def app_client_with_ollama(
    db_session: AsyncSession,
) -> AsyncIterator[AsyncClient]:
    """App client pre-seeded with Anthropic and Ollama Cloud providers.

    Begins with a DELETE-before-seed isolation step: deletes all rows from
    ModelAssignmentTable (FK-safe) then ProviderConfigTable before adding
    fresh ANTHROPIC + OLLAMA_CLOUD rows. This ensures tests are
    order-independent regardless of what prior tests committed.
    """
    app = _make_app(db_session)
    suffix = uuid4().hex[:8]
    # FK-safe cleanup: model_assignments.provider_config_id references
    # provider_configs.id, so assignments must be deleted first.
    await db_session.execute(delete(ModelAssignmentTable))
    await db_session.execute(delete(ProviderConfigTable))
    await db_session.flush()
    db_session.add(
        ProviderConfigTable(
            name=f"anthropic-test-{suffix}",
            type=ModelProvider.ANTHROPIC,
            enabled=True,
        )
    )
    db_session.add(
        ProviderConfigTable(
            name=f"ollama-test-{suffix}",
            type=ModelProvider.OLLAMA_CLOUD,
            enabled=False,
            base_url="https://ollama.example.com",
        )
    )
    await db_session.flush()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


_HDR_PM = {"X-Agent-ID": str(uuid4()), "X-Agent-Role": "main_pm"}


@pytest.mark.asyncio
async def test_get_catalog(app_client_with_ollama: AsyncClient) -> None:
    response = await app_client_with_ollama.get(
        "/api/providers/catalog", headers=_HDR_PM
    )
    assert response.status_code == HTTPStatus.OK
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_get_catalog_forbidden_for_developer(
    db_session: AsyncSession,
) -> None:
    app = _make_app(db_session, role=AgentRole.DEVELOPER, team=Team.BACKEND)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/providers/catalog",
            headers={"X-Agent-ID": str(uuid4()), "X-Agent-Role": "developer"},
        )
    assert response.status_code == HTTPStatus.FORBIDDEN
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_ollama_key_status(app_client_with_ollama: AsyncClient) -> None:
    response = await app_client_with_ollama.get(
        "/api/providers/ollama-key", headers=_HDR_PM
    )
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert "has_key" in body
    assert "enabled" in body


@pytest.mark.asyncio
async def test_set_ollama_key(app_client_with_ollama: AsyncClient) -> None:
    response = await app_client_with_ollama.put(
        "/api/providers/ollama-key",
        json={"api_key": "secret-key-123"},
        headers=_HDR_PM,
    )
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["has_key"] is True


@pytest.mark.asyncio
async def test_get_current_mode(app_client_with_ollama: AsyncClient) -> None:
    response = await app_client_with_ollama.get("/api/providers", headers=_HDR_PM)
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["mode"] in {"anthropic", "ollama", "mix"}


@pytest.mark.asyncio
async def test_apply_mode_anthropic_clears_assignments(
    app_client_with_ollama: AsyncClient,
) -> None:
    response = await app_client_with_ollama.post(
        "/api/providers", json={"mode": "anthropic"}, headers=_HDR_PM
    )
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["mode"] == "anthropic"


@pytest.mark.asyncio
async def test_apply_mode_unknown_returns_4xx(
    app_client_with_ollama: AsyncClient,
) -> None:
    """Unknown mode is rejected — Pydantic 422 at schema layer or 400 at service."""
    response = await app_client_with_ollama.post(
        "/api/providers", json={"mode": "quantum"}, headers=_HDR_PM
    )
    assert response.status_code in (
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.UNPROCESSABLE_ENTITY,
    )


@pytest.mark.asyncio
async def test_get_ollama_key_not_seeded(db_session: AsyncSession) -> None:
    """When provider not seeded, returns 404."""
    # Delete the OLLAMA_CLOUD provider
    await db_session.execute(
        delete(ProviderConfigTable).where(
            ProviderConfigTable.type == ModelProvider.OLLAMA_CLOUD
        )
    )
    await db_session.flush()

    app = _make_app(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/providers/ollama-key", headers=_HDR_PM)
    app.dependency_overrides.clear()
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_set_ollama_key_no_provider(db_session: AsyncSession) -> None:
    """Setting key with no provider raises 404."""
    await db_session.execute(
        delete(ProviderConfigTable).where(
            ProviderConfigTable.type == ModelProvider.OLLAMA_CLOUD
        )
    )
    await db_session.flush()

    app = _make_app(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/api/providers/ollama-key",
            json={"api_key": "secret"},
            headers=_HDR_PM,
        )
    app.dependency_overrides.clear()
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_set_ollama_key_developer_forbidden(
    db_session: AsyncSession,
) -> None:
    app = _make_app(db_session, role=AgentRole.DEVELOPER, team=Team.BACKEND)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/api/providers/ollama-key",
            json={"api_key": "secret"},
            headers={"X-Agent-ID": str(uuid4()), "X-Agent-Role": "developer"},
        )
    app.dependency_overrides.clear()
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_get_mode_developer_forbidden(
    db_session: AsyncSession,
) -> None:
    app = _make_app(db_session, role=AgentRole.DEVELOPER, team=Team.BACKEND)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/providers",
            headers={"X-Agent-ID": str(uuid4()), "X-Agent-Role": "developer"},
        )
    app.dependency_overrides.clear()
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_apply_mode_mix_without_per_agent_returns_400(
    app_client_with_ollama: AsyncClient,
) -> None:
    """Apply 'mix' mode without per_agent triggers ValueError → 400 (lines 149-152)."""
    response = await app_client_with_ollama.post(
        "/api/providers",
        json={"mode": "mix"},
        headers=_HDR_PM,
    )
    assert response.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_apply_mode_ollama_without_provider_returns_404(
    db_session: AsyncSession,
) -> None:
    """Apply 'ollama' mode without ollama provider raises NotFoundError → 404."""
    await db_session.execute(
        delete(ProviderConfigTable).where(
            ProviderConfigTable.type == ModelProvider.OLLAMA_CLOUD
        )
    )
    await db_session.flush()

    app = _make_app(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/providers", json={"mode": "ollama"}, headers=_HDR_PM
        )
    app.dependency_overrides.clear()
    assert response.status_code == HTTPStatus.NOT_FOUND


# =============================================================================
# Self-hosted endpoints
# =============================================================================


@pytest_asyncio.fixture
async def app_client_with_local(
    db_session: AsyncSession,
) -> AsyncIterator[AsyncClient]:
    """App client pre-seeded with Anthropic, Ollama Cloud, and LOCAL providers.

    Begins with a DELETE-before-seed isolation step: deletes all rows from
    ModelAssignmentTable (FK-safe) then ProviderConfigTable before adding
    fresh rows. This ensures tests are order-independent regardless of what
    prior tests committed.
    """
    app = _make_app(db_session)
    suffix = uuid4().hex[:8]
    # FK-safe cleanup: model_assignments.provider_config_id references
    # provider_configs.id, so assignments must be deleted first.
    await db_session.execute(delete(ModelAssignmentTable))
    await db_session.execute(delete(ProviderConfigTable))
    await db_session.flush()
    db_session.add(
        ProviderConfigTable(
            name=f"anthropic-local-{suffix}",
            type=ModelProvider.ANTHROPIC,
            enabled=True,
        )
    )
    db_session.add(
        ProviderConfigTable(
            name=f"ollama-local-{suffix}",
            type=ModelProvider.OLLAMA_CLOUD,
            enabled=False,
            base_url="https://ollama.example.com",
        )
    )
    db_session.add(
        ProviderConfigTable(
            name=f"self-hosted-local-{suffix}",
            type=ModelProvider.LOCAL,
            enabled=False,
        )
    )
    await db_session.flush()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_put_self_hosted_saves_base_url(
    app_client_with_local: AsyncClient,
) -> None:
    """PUT /self-hosted saves base_url and enables the LOCAL provider."""
    response = await app_client_with_local.put(
        "/api/providers/self-hosted",
        json={"base_url": "http://192.168.1.10:11434"},
        headers=_HDR_PM,
    )
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["base_url"] == "http://192.168.1.10:11434"
    assert body["enabled"] is True
    assert body["has_token"] is False


@pytest.mark.asyncio
async def test_put_self_hosted_with_token_stores_encrypted(
    app_client_with_local: AsyncClient,
) -> None:
    """PUT /self-hosted with auth_token stores Fernet-encrypted token."""
    response = await app_client_with_local.put(
        "/api/providers/self-hosted",
        json={
            "base_url": "http://192.168.1.10:11434",
            "auth_token": "secret-ollama-key",
        },
        headers=_HDR_PM,
    )
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["has_token"] is True


@pytest.mark.asyncio
async def test_put_self_hosted_not_seeded_returns_404(
    db_session: AsyncSession,
) -> None:
    """PUT /self-hosted when LOCAL provider not seeded returns 404."""
    await db_session.execute(
        delete(ProviderConfigTable).where(
            ProviderConfigTable.type == ModelProvider.LOCAL
        )
    )
    await db_session.flush()

    app = _make_app(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/api/providers/self-hosted",
            json={"base_url": "http://localhost:11434"},
            headers=_HDR_PM,
        )
    app.dependency_overrides.clear()
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_put_self_hosted_developer_forbidden(
    db_session: AsyncSession,
) -> None:
    """PUT /self-hosted is forbidden for developer role."""
    app = _make_app(db_session, role=AgentRole.DEVELOPER, team=Team.BACKEND)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/api/providers/self-hosted",
            json={"base_url": "http://localhost:11434"},
            headers={"X-Agent-ID": str(uuid4()), "X-Agent-Role": "developer"},
        )
    app.dependency_overrides.clear()
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_post_test_self_hosted_when_reachable(
    app_client_with_local: AsyncClient,
) -> None:
    """POST /self-hosted/test returns {ok: true, model_count: N} when reachable."""
    # First configure the base_url.
    await app_client_with_local.put(
        "/api/providers/self-hosted",
        json={"base_url": "http://192.168.1.10:11434"},
        headers=_HDR_PM,
    )
    with patch(
        "roboco.api.routes.provider.probe_ollama_tags",
        new_callable=AsyncMock,
        return_value=(["llama3.1:8b", "gemma2:9b"], None),
    ):
        response = await app_client_with_local.post(
            "/api/providers/self-hosted/test",
            headers=_HDR_PM,
        )
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    # Contract: field names and types for the test response schema.
    assert "ok" in body
    assert "model_count" in body
    assert "error" in body
    assert isinstance(body["ok"], bool)
    assert body["ok"] is True
    assert body["model_count"] == 2  # noqa: PLR2004
    assert body["error"] is None


@pytest.mark.asyncio
async def test_post_test_self_hosted_when_unreachable(
    app_client_with_local: AsyncClient,
) -> None:
    """POST /self-hosted/test returns {ok: false, error: '...'} when unreachable."""
    await app_client_with_local.put(
        "/api/providers/self-hosted",
        json={"base_url": "http://192.168.1.10:11434"},
        headers=_HDR_PM,
    )
    with patch(
        "roboco.api.routes.provider.probe_ollama_tags",
        new_callable=AsyncMock,
        return_value=([], "Could not connect to http://192.168.1.10:11434"),
    ):
        response = await app_client_with_local.post(
            "/api/providers/self-hosted/test",
            headers=_HDR_PM,
        )
    # Must be 200 with ok=false, NOT 500.
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["ok"] is False
    assert body["error"] is not None
    assert body["model_count"] is None


@pytest.mark.asyncio
async def test_post_test_self_hosted_not_configured(
    app_client_with_local: AsyncClient,
) -> None:
    """POST /self-hosted/test when no base_url returns {ok: false} without 500."""
    # LOCAL provider seeded but no base_url configured.
    response = await app_client_with_local.post(
        "/api/providers/self-hosted/test",
        headers=_HDR_PM,
    )
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["ok"] is False
    assert body["error"] is not None


@pytest.mark.asyncio
async def test_get_self_hosted_config_returns_200(
    app_client_with_local: AsyncClient,
) -> None:
    """GET /self-hosted returns {base_url, has_token, enabled} when LOCAL is seeded."""
    response = await app_client_with_local.get(
        "/api/providers/self-hosted",
        headers=_HDR_PM,
    )
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    # Contract: field names and types must match the schema.
    assert "base_url" in body
    assert "has_token" in body
    assert "enabled" in body
    assert isinstance(body["has_token"], bool)
    assert isinstance(body["enabled"], bool)


@pytest.mark.asyncio
async def test_get_self_hosted_config_not_seeded_returns_404(
    db_session: AsyncSession,
) -> None:
    """GET /self-hosted when LOCAL provider not seeded returns 404."""
    await db_session.execute(
        delete(ProviderConfigTable).where(
            ProviderConfigTable.type == ModelProvider.LOCAL
        )
    )
    await db_session.flush()

    app = _make_app(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/providers/self-hosted",
            headers=_HDR_PM,
        )
    app.dependency_overrides.clear()
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_get_self_hosted_models_returns_list(
    app_client_with_local: AsyncClient,
) -> None:
    """GET /self-hosted/models returns [{model_name, display_name}] objects."""
    await app_client_with_local.put(
        "/api/providers/self-hosted",
        json={"base_url": "http://192.168.1.10:11434"},
        headers=_HDR_PM,
    )
    with patch(
        "roboco.api.routes.provider.probe_ollama_tags",
        new_callable=AsyncMock,
        return_value=(["llama3.1:8b", "gemma2:9b", "qwen2.5:14b"], None),
    ):
        response = await app_client_with_local.get(
            "/api/providers/self-hosted/models",
            headers=_HDR_PM,
        )
    assert response.status_code == HTTPStatus.OK
    models = response.json()
    assert isinstance(models, list)
    assert len(models) == 3  # noqa: PLR2004
    # Contract: each entry must be an object with model_name and display_name.
    first = models[0]
    assert isinstance(first, dict)
    assert "model_name" in first
    assert "display_name" in first
    assert isinstance(first["model_name"], str)
    assert isinstance(first["display_name"], str)
    # Verify specific entry present.
    names = [m["model_name"] for m in models]
    assert "llama3.1:8b" in names


@pytest.mark.asyncio
async def test_get_self_hosted_models_not_configured_returns_404(
    app_client_with_local: AsyncClient,
) -> None:
    """GET /self-hosted/models when no base_url configured returns 404."""
    response = await app_client_with_local.get(
        "/api/providers/self-hosted/models",
        headers=_HDR_PM,
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_get_self_hosted_models_unreachable_returns_503(
    app_client_with_local: AsyncClient,
) -> None:
    """GET /self-hosted/models when server unreachable returns 503."""
    await app_client_with_local.put(
        "/api/providers/self-hosted",
        json={"base_url": "http://192.168.1.10:11434"},
        headers=_HDR_PM,
    )
    with patch(
        "roboco.api.routes.provider.probe_ollama_tags",
        new_callable=AsyncMock,
        return_value=([], "Could not connect"),
    ):
        response = await app_client_with_local.get(
            "/api/providers/self-hosted/models",
            headers=_HDR_PM,
        )
    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
