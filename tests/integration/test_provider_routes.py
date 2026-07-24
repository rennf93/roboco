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
from roboco.billing.pricing import input_price_per_million
from roboco.db.tables import (
    ModelAssignmentTable,
    ProviderConfigTable,
    RoutingPresetTable,
)
from roboco.models import AgentRole, Team
from roboco.models.base import AssignmentScope, ModelProvider
from roboco.models.llm_catalog import MODEL_CATALOG
from roboco.models.permissions import AgentContext
from sqlalchemy import delete, select

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


def _unpriced_model_for_type(provider_type: ModelProvider) -> str:
    """The first `provider_type` catalog entry pricing.py has NOT grounded a
    real per-token rate for — tests below want "an unpriced, free-tier
    downgrade-safe model" specifically to exercise provider-readiness gating,
    not pricing itself, so grounding a real rate for one catalog entry (e.g.
    GLM-5.2) must not silently break them by picking that one."""
    for entry in MODEL_CATALOG:
        if (
            entry.provider_type == provider_type
            and input_price_per_million(entry.model_name) == 0.0
        ):
            return entry.model_name
    raise RuntimeError(f"no unpriced catalog entry for {provider_type}")


def _make_app(
    db_session: AsyncSession,
    role: AgentRole = AgentRole.MAIN_PM,
    team: Team | None = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(provider_router, prefix="/api/providers")

    async def _override_db() -> AsyncIterator[AsyncSession]:
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
    ModelAssignmentTable (FK-safe), ProviderConfigTable, and RoutingPresetTable
    before adding fresh ANTHROPIC + OLLAMA_CLOUD rows. This ensures tests are
    order-independent regardless of what prior tests committed — the fixture's
    `db.commit()` calls are real commits against the session-scoped scratch
    DB (`db_session`'s teardown only rolls back uncommitted state), so without
    this every table a test writes through a route's `db.commit()` needs its
    own cleanup here, RoutingPresetTable included.
    """
    app = _make_app(db_session)
    suffix = uuid4().hex[:8]
    # FK-safe cleanup: model_assignments.provider_config_id references
    # provider_configs.id, so assignments must be deleted first.
    await db_session.execute(delete(ModelAssignmentTable))
    await db_session.execute(delete(ProviderConfigTable))
    await db_session.execute(delete(RoutingPresetTable))
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


@pytest_asyncio.fixture
async def app_client_with_codex_and_gemini(
    db_session: AsyncSession,
) -> AsyncIterator[AsyncClient]:
    """App client pre-seeded with Anthropic + disabled OPENAI/GEMINI providers
    (mirrors the real seeded state before an operator ever applies either
    mode: OPENAI seeds enabled=true per migration 083, GEMINI seeds
    enabled=false per migration 085 — deliberately seeded disabled here so the
    apply-mode round trip below proves the force-enable, not a pre-enabled
    no-op)."""
    app = _make_app(db_session)
    suffix = uuid4().hex[:8]
    await db_session.execute(delete(ModelAssignmentTable))
    await db_session.execute(delete(ProviderConfigTable))
    await db_session.flush()
    db_session.add(
        ProviderConfigTable(
            name=f"anthropic-cg-{suffix}", type=ModelProvider.ANTHROPIC, enabled=True
        )
    )
    db_session.add(
        ProviderConfigTable(
            name=f"codex-cg-{suffix}",
            type=ModelProvider.OPENAI,
            enabled=False,
            base_url="https://api.openai.com/v1",
        )
    )
    db_session.add(
        ProviderConfigTable(
            name=f"gemini-cg-{suffix}", type=ModelProvider.GEMINI, enabled=False
        )
    )
    await db_session.flush()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_apply_mode_codex_returns_200_reflects_mode_and_enables_provider(
    app_client_with_codex_and_gemini: AsyncClient,
) -> None:
    """The full HTTP round trip: POST mode="codex" -> 200, GET reflects
    mode="codex", and the assignment resolves through the now-enabled OPENAI
    provider — proving the pydantic Literal + dispatch + enable chain end to
    end, not just the service-layer call this mirrors."""
    response = await app_client_with_codex_and_gemini.post(
        "/api/providers", json={"mode": "codex"}, headers=_HDR_PM
    )
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["mode"] == "codex"
    assert body["assignments"][0]["provider_type"] == "openai"
    assert body["assignments"][0]["model_name"] == "gpt-5.3-codex"

    followup = await app_client_with_codex_and_gemini.get(
        "/api/providers", headers=_HDR_PM
    )
    assert followup.json()["mode"] == "codex"


@pytest.mark.asyncio
async def test_apply_mode_gemini_returns_200_reflects_mode_and_enables_provider(
    app_client_with_codex_and_gemini: AsyncClient,
) -> None:
    """Same round trip as Codex's, for Gemini — the exact reachability gap
    this fix closes (the row seeds disabled and nothing else ever flipped it)."""
    response = await app_client_with_codex_and_gemini.post(
        "/api/providers", json={"mode": "gemini"}, headers=_HDR_PM
    )
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["mode"] == "gemini"
    assert body["assignments"][0]["provider_type"] == "gemini"
    assert body["assignments"][0]["model_name"] == "gemini-2.5-pro"

    followup = await app_client_with_codex_and_gemini.get(
        "/api/providers", headers=_HDR_PM
    )
    assert followup.json()["mode"] == "gemini"


@pytest.mark.asyncio
async def test_apply_mode_gemini_without_provider_returns_404(
    db_session: AsyncSession,
) -> None:
    """Apply 'gemini' mode without the GEMINI provider seeded raises
    NotFoundError -> 404 (mirrors the ollama/grok equivalents)."""
    # FK-safe: a prior test may have committed a real GEMINI assignment
    # (model_assignments.provider_config_id references provider_configs.id),
    # so assignments must be cleared before the provider row can be deleted.
    await db_session.execute(delete(ModelAssignmentTable))
    await db_session.execute(
        delete(ProviderConfigTable).where(
            ProviderConfigTable.type == ModelProvider.GEMINI
        )
    )
    await db_session.flush()

    app = _make_app(db_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/providers", json={"mode": "gemini"}, headers=_HDR_PM
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


# =============================================================================
# Complexity overrides (cost-tiered routing: compound ROLE(":"complexity) rows)
# =============================================================================


@pytest.mark.asyncio
async def test_get_complexity_overrides_empty(
    app_client_with_ollama: AsyncClient,
) -> None:
    response = await app_client_with_ollama.get(
        "/api/providers/complexity-overrides", headers=_HDR_PM
    )
    assert response.status_code == HTTPStatus.OK
    assert response.json() == []


@pytest.mark.asyncio
async def test_put_complexity_override_round_trips_through_get(
    app_client_with_ollama: AsyncClient,
) -> None:
    """PUT developer:low -> haiku (no costlier than the sonnet baseline)."""
    response = await app_client_with_ollama.put(
        "/api/providers/complexity-overrides",
        json={"role": "developer", "complexity": "low", "model_name": "haiku"},
        headers=_HDR_PM,
    )
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body == {
        "role": "developer",
        "complexity": "low",
        "model_name": "haiku",
        "warning": None,
    }

    listing = await app_client_with_ollama.get(
        "/api/providers/complexity-overrides", headers=_HDR_PM
    )
    # GET's listing rows are never constructed WITH a warning (only PUT
    # computes one), but the shared response schema still serializes the
    # field at its None default.
    assert listing.json() == [body]


@pytest.mark.asyncio
async def test_put_complexity_override_rejects_disallowed_role(
    app_client_with_ollama: AsyncClient,
) -> None:
    """main_pm is a coordinator role — never offered a complexity override."""
    response = await app_client_with_ollama.put(
        "/api/providers/complexity-overrides",
        json={"role": "main_pm", "complexity": "low", "model_name": "haiku"},
        headers=_HDR_PM,
    )
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert "deliberate" in response.json()["detail"]


@pytest.mark.asyncio
async def test_put_complexity_override_rejects_costlier_tier(
    app_client_with_ollama: AsyncClient,
) -> None:
    """developer's baseline is sonnet — opus is a costlier tier, rejected."""
    response = await app_client_with_ollama.put(
        "/api/providers/complexity-overrides",
        json={"role": "developer", "complexity": "high", "model_name": "opus"},
        headers=_HDR_PM,
    )
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert "downgrade-only" in response.json()["detail"]


@pytest.mark.asyncio
async def test_put_complexity_override_allows_same_tier_as_baseline(
    app_client_with_ollama: AsyncClient,
) -> None:
    """A same-tier pin (sonnet for developer, whose baseline IS sonnet) is not
    a downgrade but isn't costlier either — allowed."""
    response = await app_client_with_ollama.put(
        "/api/providers/complexity-overrides",
        json={"role": "developer", "complexity": "high", "model_name": "sonnet"},
        headers=_HDR_PM,
    )
    assert response.status_code == HTTPStatus.OK
    assert response.json()["warning"] is None


@pytest.mark.asyncio
async def test_put_complexity_override_rejects_disabled_provider(
    app_client_with_ollama: AsyncClient,
) -> None:
    """qa's baseline (haiku) prices no cheaper than an unpriced Ollama Cloud
    model (free-tier) so the downgrade-only check passes — but the
    OLLAMA_CLOUD provider is disabled (no key set) in this fixture's seeded
    state, so the write-time readiness guard rejects it before it can
    silently no-op to the legacy Anthropic path at spawn."""
    ollama_model = _unpriced_model_for_type(ModelProvider.OLLAMA_CLOUD)
    response = await app_client_with_ollama.put(
        "/api/providers/complexity-overrides",
        json={"role": "qa", "complexity": "low", "model_name": ollama_model},
        headers=_HDR_PM,
    )
    assert response.status_code == HTTPStatus.BAD_REQUEST
    detail = response.json()["detail"]
    assert "isn't configured yet" in detail
    assert "Ollama" in detail


@pytest.mark.asyncio
async def test_put_complexity_override_warns_on_cross_family_once_provider_ready(
    app_client_with_ollama: AsyncClient,
) -> None:
    """Once Ollama Cloud is enabled (key set), the same cross-family override
    succeeds — allowed, but the response carries a non-null warning since
    it's a different provider family than qa's Anthropic baseline."""
    await app_client_with_ollama.put(
        "/api/providers/ollama-key",
        json={"api_key": "test-key"},
        headers=_HDR_PM,
    )
    ollama_model = _unpriced_model_for_type(ModelProvider.OLLAMA_CLOUD)
    response = await app_client_with_ollama.put(
        "/api/providers/complexity-overrides",
        json={"role": "qa", "complexity": "low", "model_name": ollama_model},
        headers=_HDR_PM,
    )
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["warning"] is not None
    assert "ollama_cloud" in body["warning"]
    assert "qa" in body["warning"]


@pytest.mark.asyncio
async def test_put_complexity_override_developer_forbidden(
    db_session: AsyncSession,
) -> None:
    app = _make_app(db_session, role=AgentRole.DEVELOPER, team=Team.BACKEND)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/api/providers/complexity-overrides",
            json={"role": "developer", "complexity": "low", "model_name": "haiku"},
            headers={"X-Agent-ID": str(uuid4()), "X-Agent-Role": "developer"},
        )
    app.dependency_overrides.clear()
    assert response.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_delete_complexity_override(
    app_client_with_ollama: AsyncClient,
) -> None:
    await app_client_with_ollama.put(
        "/api/providers/complexity-overrides",
        json={"role": "qa", "complexity": "high", "model_name": "haiku"},
        headers=_HDR_PM,
    )
    response = await app_client_with_ollama.delete(
        "/api/providers/complexity-overrides/qa/high", headers=_HDR_PM
    )
    assert response.status_code == HTTPStatus.NO_CONTENT

    listing = await app_client_with_ollama.get(
        "/api/providers/complexity-overrides", headers=_HDR_PM
    )
    assert listing.json() == []


@pytest.mark.asyncio
async def test_delete_complexity_override_not_found(
    app_client_with_ollama: AsyncClient,
) -> None:
    response = await app_client_with_ollama.delete(
        "/api/providers/complexity-overrides/qa/low", headers=_HDR_PM
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_apply_mode_cost_tiered_seeds_day1_rows(
    app_client_with_ollama: AsyncClient,
) -> None:
    response = await app_client_with_ollama.post(
        "/api/providers", json={"mode": "cost_tiered"}, headers=_HDR_PM
    )
    assert response.status_code == HTTPStatus.OK

    listing = await app_client_with_ollama.get(
        "/api/providers/complexity-overrides", headers=_HDR_PM
    )
    rows = {(r["role"], r["complexity"]): r["model_name"] for r in listing.json()}
    # Seed retired (2026-07-24): its only entry was developer:low -> haiku,
    # which the structured-verb capability floor would upgrade to sonnet
    # anyway. cost_tiered mode stays wired but seeds nothing now.
    assert rows == {}


@pytest.mark.asyncio
async def test_apply_mode_cost_tiered_is_additive_preserves_global(
    app_client_with_ollama: AsyncClient,
) -> None:
    """Unlike every other mode, cost_tiered never wipes existing rows."""
    await app_client_with_ollama.post(
        "/api/providers", json={"mode": "ollama"}, headers=_HDR_PM
    )
    mode_before = (
        await app_client_with_ollama.get("/api/providers", headers=_HDR_PM)
    ).json()
    assert mode_before["mode"] == "ollama"

    response = await app_client_with_ollama.post(
        "/api/providers", json={"mode": "cost_tiered"}, headers=_HDR_PM
    )
    assert response.status_code == HTTPStatus.OK
    assignments = response.json()["assignments"]
    scopes = {(a["scope"], a["scope_value"]) for a in assignments}
    # The pre-existing GLOBAL row from 'ollama' mode survives untouched — the
    # non-wiping contract holds even with the seed retired to empty.
    assert ("global", None) in scopes


# =============================================================================
# Routing presets (named, full snapshots of the routing state)
# =============================================================================


@pytest.mark.asyncio
async def test_save_list_and_apply_preset_round_trip(
    app_client_with_ollama: AsyncClient,
) -> None:
    """Save captures the current state; mutating + re-applying restores it."""
    # Set the Ollama key first — the fixture seeds OLLAMA_CLOUD `enabled=False`
    # (no key yet), and `_validate_preset_entry` now rejects (skip-with-note)
    # any preset entry whose provider is disabled, so a meaningful round trip
    # needs the provider actually live, same as the real UI's key-gated mode
    # button.
    await app_client_with_ollama.put(
        "/api/providers/ollama-key", json={"api_key": "test-key"}, headers=_HDR_PM
    )
    # Arrange a distinctive state: a GLOBAL Ollama default.
    await app_client_with_ollama.post(
        "/api/providers", json={"mode": "ollama"}, headers=_HDR_PM
    )
    snapshot_before = (
        await app_client_with_ollama.get("/api/providers", headers=_HDR_PM)
    ).json()

    save_resp = await app_client_with_ollama.post(
        "/api/providers/presets", json={"name": "my-preset"}, headers=_HDR_PM
    )
    assert save_resp.status_code == HTTPStatus.OK
    preset_id = save_resp.json()["id"]
    assert save_resp.json()["name"] == "my-preset"

    listing = await app_client_with_ollama.get(
        "/api/providers/presets", headers=_HDR_PM
    )
    assert listing.status_code == HTTPStatus.OK
    assert [p["name"] for p in listing.json()] == ["my-preset"]

    # Mutate away from the saved state.
    await app_client_with_ollama.post(
        "/api/providers", json={"mode": "anthropic"}, headers=_HDR_PM
    )
    mutated = (
        await app_client_with_ollama.get("/api/providers", headers=_HDR_PM)
    ).json()
    assert mutated["assignments"] == []

    # Apply the preset back — restores the snapshot. Applying always
    # deletes-then-reinserts (a real full swap), so row `id`s are fresh;
    # compare on the business fields only.
    apply_resp = await app_client_with_ollama.post(
        f"/api/providers/presets/{preset_id}/apply", headers=_HDR_PM
    )
    assert apply_resp.status_code == HTTPStatus.OK
    applied = apply_resp.json()
    assert applied["skipped"] == []

    def _sans_id(assignments: list[dict]) -> list[dict]:
        return [{k: v for k, v in a.items() if k != "id"} for a in assignments]

    assert _sans_id(applied["assignments"]) == _sans_id(snapshot_before["assignments"])


@pytest.mark.asyncio
async def test_save_preset_duplicate_name_returns_409(
    app_client_with_ollama: AsyncClient,
) -> None:
    first = await app_client_with_ollama.post(
        "/api/providers/presets", json={"name": "dup"}, headers=_HDR_PM
    )
    assert first.status_code == HTTPStatus.OK
    second = await app_client_with_ollama.post(
        "/api/providers/presets", json={"name": "dup"}, headers=_HDR_PM
    )
    assert second.status_code == HTTPStatus.CONFLICT


@pytest.mark.asyncio
async def test_apply_preset_not_found_returns_404(
    app_client_with_ollama: AsyncClient,
) -> None:
    response = await app_client_with_ollama.post(
        f"/api/providers/presets/{uuid4()}/apply", headers=_HDR_PM
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_delete_preset_not_found_returns_404(
    app_client_with_ollama: AsyncClient,
) -> None:
    response = await app_client_with_ollama.delete(
        f"/api/providers/presets/{uuid4()}", headers=_HDR_PM
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_delete_preset(app_client_with_ollama: AsyncClient) -> None:
    save_resp = await app_client_with_ollama.post(
        "/api/providers/presets", json={"name": "to-delete"}, headers=_HDR_PM
    )
    preset_id = save_resp.json()["id"]

    response = await app_client_with_ollama.delete(
        f"/api/providers/presets/{preset_id}", headers=_HDR_PM
    )
    assert response.status_code == HTTPStatus.NO_CONTENT

    listing = await app_client_with_ollama.get(
        "/api/providers/presets", headers=_HDR_PM
    )
    assert listing.json() == []


@pytest.mark.asyncio
async def test_apply_preset_skips_entry_with_since_removed_model(
    app_client_with_ollama: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Payload hygiene: an entry referencing a model no longer in the catalog
    (and not routable to LOCAL, since no LOCAL provider is seeded here) is
    skipped with a note — never fails the whole apply."""
    row = RoutingPresetTable(
        name="stale-preset",
        payload={
            "mode": "mix",
            "assignments": [
                {
                    "scope": AssignmentScope.GLOBAL.value,
                    "scope_value": None,
                    "provider_type": "anthropic",
                    "model_name": "sonnet",
                },
                {
                    "scope": AssignmentScope.ROLE.value,
                    "scope_value": "developer",
                    "provider_type": "anthropic",
                    "model_name": "ghost-model-that-no-longer-exists",
                },
            ],
        },
    )
    db_session.add(row)
    await db_session.flush()

    response = await app_client_with_ollama.post(
        f"/api/providers/presets/{row.id}/apply", headers=_HDR_PM
    )
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert len(body["skipped"]) == 1
    assert "ghost-model-that-no-longer-exists" in body["skipped"][0]
    # The valid GLOBAL entry still applied despite the sibling failure.
    scopes = {(a["scope"], a["scope_value"]) for a in body["assignments"]}
    assert ("global", None) in scopes
    assert ("role", "developer") not in scopes


@pytest.mark.asyncio
async def test_presets_developer_forbidden(
    db_session: AsyncSession,
) -> None:
    app = _make_app(db_session, role=AgentRole.DEVELOPER, team=Team.BACKEND)
    transport = ASGITransport(app=app)
    hdr = {"X-Agent-ID": str(uuid4()), "X-Agent-Role": "developer"}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/providers/presets", headers=hdr)
    app.dependency_overrides.clear()
    assert response.status_code == HTTPStatus.FORBIDDEN
