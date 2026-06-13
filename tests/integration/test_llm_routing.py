"""ModelRoutingService coverage — assignment CRUD + mode application + resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from roboco.db.tables import ProviderConfigTable
from roboco.models.base import AssignmentScope, ModelProvider
from roboco.models.llm_catalog import MODEL_CATALOG
from roboco.services.base import NotFoundError
from roboco.services.llm import ModelRoutingService, get_model_routing_service
from roboco.utils.crypto import EncryptionError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


def _first_model_for_type(provider_type: ModelProvider) -> str:
    for entry in MODEL_CATALOG:
        if entry.provider_type == provider_type:
            return entry.model_name
    raise RuntimeError(f"no catalog entry for {provider_type}")


@pytest_asyncio.fixture
async def llm_setup(
    db_session: AsyncSession,
) -> AsyncIterator[dict]:
    """Seed the two provider rows the routing service expects."""
    anthropic = ProviderConfigTable(
        name="anthropic-test",
        type=ModelProvider.ANTHROPIC,
        enabled=True,
    )
    ollama = ProviderConfigTable(
        name="ollama-test",
        type=ModelProvider.OLLAMA_CLOUD,
        enabled=True,
        base_url="https://ollama.example.com",
    )
    db_session.add_all([anthropic, ollama])
    await db_session.flush()
    yield {"svc": ModelRoutingService(db_session)}


# ---------------------------------------------------------------------------
# Assignment CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_assignments_empty(llm_setup: dict) -> None:
    svc = llm_setup["svc"]
    assert await svc.list_assignments() == []


@pytest.mark.asyncio
async def test_upsert_global_assignment(llm_setup: dict) -> None:
    svc = llm_setup["svc"]
    model = _first_model_for_type(ModelProvider.ANTHROPIC)
    row = await svc.upsert_assignment(
        scope=AssignmentScope.GLOBAL, scope_value=None, model_name=model
    )
    assert row.scope == AssignmentScope.GLOBAL
    assert row.model_name == model


@pytest.mark.asyncio
async def test_upsert_role_assignment(llm_setup: dict) -> None:
    svc = llm_setup["svc"]
    model = _first_model_for_type(ModelProvider.ANTHROPIC)
    row = await svc.upsert_assignment(
        scope=AssignmentScope.ROLE, scope_value="developer", model_name=model
    )
    assert row.scope == AssignmentScope.ROLE
    assert row.scope_value == "developer"


@pytest.mark.asyncio
async def test_upsert_replaces_existing(llm_setup: dict) -> None:
    svc = llm_setup["svc"]
    anth_model = _first_model_for_type(ModelProvider.ANTHROPIC)
    ollama_model = _first_model_for_type(ModelProvider.OLLAMA_CLOUD)
    a = await svc.upsert_assignment(
        scope=AssignmentScope.GLOBAL, scope_value=None, model_name=anth_model
    )
    b = await svc.upsert_assignment(
        scope=AssignmentScope.GLOBAL, scope_value=None, model_name=ollama_model
    )
    assert a.id == b.id  # Updated, not duplicated.
    assert b.model_name == ollama_model


@pytest.mark.asyncio
async def test_upsert_unknown_model_raises(llm_setup: dict) -> None:
    svc = llm_setup["svc"]
    with pytest.raises(ValueError, match="Unknown model"):
        await svc.upsert_assignment(
            scope=AssignmentScope.GLOBAL,
            scope_value=None,
            model_name="ghost-model",
        )


@pytest.mark.asyncio
async def test_upsert_invalid_global_scope_raises(llm_setup: dict) -> None:
    svc = llm_setup["svc"]
    model = _first_model_for_type(ModelProvider.ANTHROPIC)
    with pytest.raises(ValueError, match="global scope must"):
        await svc.upsert_assignment(
            scope=AssignmentScope.GLOBAL, scope_value="not-none", model_name=model
        )


@pytest.mark.asyncio
async def test_upsert_role_requires_value(llm_setup: dict) -> None:
    svc = llm_setup["svc"]
    model = _first_model_for_type(ModelProvider.ANTHROPIC)
    with pytest.raises(ValueError, match="requires a non-empty"):
        await svc.upsert_assignment(
            scope=AssignmentScope.ROLE, scope_value=None, model_name=model
        )


@pytest.mark.asyncio
async def test_get_assignment_returns_none_when_missing(llm_setup: dict) -> None:
    svc = llm_setup["svc"]
    assert (
        await svc.get_assignment(scope=AssignmentScope.AGENT_SLUG, scope_value="ghost")
        is None
    )


@pytest.mark.asyncio
async def test_delete_assignment(llm_setup: dict) -> None:
    svc = llm_setup["svc"]
    model = _first_model_for_type(ModelProvider.ANTHROPIC)
    await svc.upsert_assignment(
        scope=AssignmentScope.GLOBAL, scope_value=None, model_name=model
    )
    await svc.delete_assignment(scope=AssignmentScope.GLOBAL, scope_value=None)
    assert (
        await svc.get_assignment(scope=AssignmentScope.GLOBAL, scope_value=None) is None
    )


@pytest.mark.asyncio
async def test_delete_assignment_raises_when_missing(llm_setup: dict) -> None:
    svc = llm_setup["svc"]
    with pytest.raises(NotFoundError):
        await svc.delete_assignment(scope=AssignmentScope.GLOBAL, scope_value=None)


# ---------------------------------------------------------------------------
# derive_mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_derive_mode_anthropic_when_no_assignments(llm_setup: dict) -> None:
    svc = llm_setup["svc"]
    assert await svc.derive_mode() == "anthropic"


@pytest.mark.asyncio
async def test_derive_mode_ollama_when_only_ollama_global(llm_setup: dict) -> None:
    svc = llm_setup["svc"]
    ollama_model = _first_model_for_type(ModelProvider.OLLAMA_CLOUD)
    await svc.upsert_assignment(
        scope=AssignmentScope.GLOBAL, scope_value=None, model_name=ollama_model
    )
    assert await svc.derive_mode() == "ollama"


@pytest.mark.asyncio
async def test_derive_mode_mix_with_per_agent(llm_setup: dict) -> None:
    svc = llm_setup["svc"]
    model = _first_model_for_type(ModelProvider.ANTHROPIC)
    await svc.upsert_assignment(
        scope=AssignmentScope.AGENT_SLUG,
        scope_value="be-dev-1",
        model_name=model,
    )
    assert await svc.derive_mode() == "mix"


# ---------------------------------------------------------------------------
# apply_mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_mode_anthropic_clears_all(llm_setup: dict) -> None:
    svc = llm_setup["svc"]
    model = _first_model_for_type(ModelProvider.ANTHROPIC)
    await svc.upsert_assignment(
        scope=AssignmentScope.GLOBAL, scope_value=None, model_name=model
    )
    await svc.apply_mode(mode="anthropic")
    assert await svc.list_assignments() == []


@pytest.mark.asyncio
async def test_apply_mode_ollama_sets_global(llm_setup: dict) -> None:
    svc = llm_setup["svc"]
    ollama_model = _first_model_for_type(ModelProvider.OLLAMA_CLOUD)
    await svc.apply_mode(mode="ollama", default_model=ollama_model)
    assignments = await svc.list_assignments()
    assert len(assignments) == 1
    assert assignments[0].scope == AssignmentScope.GLOBAL


@pytest.mark.asyncio
async def test_apply_mode_mix_requires_per_agent(llm_setup: dict) -> None:
    svc = llm_setup["svc"]
    with pytest.raises(ValueError, match="requires a per_agent"):
        await svc.apply_mode(mode="mix")


@pytest.mark.asyncio
async def test_apply_mode_mix_writes_overrides(llm_setup: dict) -> None:
    svc = llm_setup["svc"]
    model = _first_model_for_type(ModelProvider.ANTHROPIC)
    await svc.apply_mode(
        mode="mix",
        per_agent={"be-dev-1": model, "be-dev-2": model, "skip-me": ""},
    )
    rows = await svc.list_assignments()
    slugs = {r.scope_value for r in rows if r.scope == AssignmentScope.AGENT_SLUG}
    assert "be-dev-1" in slugs
    assert "be-dev-2" in slugs
    # Empty model_name skipped.
    assert "skip-me" not in slugs


@pytest.mark.asyncio
async def test_apply_mode_unknown_raises(llm_setup: dict) -> None:
    svc = llm_setup["svc"]
    with pytest.raises(ValueError, match="Unknown mode"):
        await svc.apply_mode(mode="quantum")


# ---------------------------------------------------------------------------
# Ollama API key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_ollama_api_key_encrypts(llm_setup: dict) -> None:
    svc = llm_setup["svc"]
    provider = await svc.set_ollama_api_key("secret-key-123")
    assert provider.auth_token_encrypted is not None
    assert provider.enabled is True


@pytest.mark.asyncio
async def test_set_ollama_api_key_clears(llm_setup: dict) -> None:
    svc = llm_setup["svc"]
    await svc.set_ollama_api_key("secret-key-123")
    cleared = await svc.set_ollama_api_key("")
    assert cleared.auth_token_encrypted is None
    assert cleared.enabled is False


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_for_agent_legacy_fallback_when_no_assignments(
    llm_setup: dict,
) -> None:
    """No assignments → Anthropic with a model from MODEL_MAP, no auth_token."""
    svc = llm_setup["svc"]
    route = await svc.resolve_for_agent("be-dev-1")
    assert route.provider_type == ModelProvider.ANTHROPIC
    assert route.auth_token is None  # Container uses mounted creds.
    assert route.model_name  # Always resolved.


@pytest.mark.asyncio
async def test_resolve_for_agent_uses_global_assignment(
    llm_setup: dict,
) -> None:
    svc = llm_setup["svc"]
    model = _first_model_for_type(ModelProvider.ANTHROPIC)
    await svc.upsert_assignment(
        scope=AssignmentScope.GLOBAL, scope_value=None, model_name=model
    )
    route = await svc.resolve_for_agent("be-dev-1")
    assert route.model_name == model


@pytest.mark.asyncio
async def test_resolve_for_agent_uses_provider_token(llm_setup: dict) -> None:
    """When provider has auth_token_encrypted, it's decrypted (lines 345-346)."""
    svc = llm_setup["svc"]
    await svc.set_ollama_api_key("test-secret-key")
    ollama_model = _first_model_for_type(ModelProvider.OLLAMA_CLOUD)
    await svc.upsert_assignment(
        scope=AssignmentScope.GLOBAL, scope_value=None, model_name=ollama_model
    )
    route = await svc.resolve_for_agent("be-dev-1")
    assert route.auth_token == "test-secret-key"


@pytest.mark.asyncio
async def test_get_seeded_provider_unknown_raises(
    db_session: AsyncSession,
) -> None:
    """When the requested provider type isn't seeded, raise NotFoundError (line 238)."""
    svc = ModelRoutingService(db_session)
    with pytest.raises(NotFoundError):
        await svc._get_seeded_provider(ModelProvider.ANTHROPIC)


def test_get_model_routing_service_factory(db_session: AsyncSession) -> None:
    """Factory wraps ModelRoutingService with the given session (line 369)."""

    svc = get_model_routing_service(db_session)
    assert isinstance(svc, ModelRoutingService)


@pytest.mark.asyncio
async def test_resolve_for_agent_falls_back_on_decrypt_error(
    llm_setup: dict,
) -> None:
    """EncryptionError on decrypt → falls back to legacy path (lines 98-99)."""

    svc = llm_setup["svc"]
    await svc.set_ollama_api_key("garbage-token")
    ollama_model = _first_model_for_type(ModelProvider.OLLAMA_CLOUD)
    await svc.upsert_assignment(
        scope=AssignmentScope.GLOBAL, scope_value=None, model_name=ollama_model
    )
    with patch(
        "roboco.services.llm.ProviderService.get_decrypted_token",
        side_effect=EncryptionError("decrypt"),
        new_callable=AsyncMock,
    ):
        route = await svc.resolve_for_agent("be-dev-1")
    # Falls back to legacy ANTHROPIC route.
    assert route.provider_type == ModelProvider.ANTHROPIC


# ---------------------------------------------------------------------------
# Self-hosted (LOCAL) provider
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def llm_setup_with_local(
    db_session: AsyncSession,
) -> AsyncIterator[dict]:
    """Seed Anthropic, Ollama Cloud, and LOCAL provider rows."""
    anthropic = ProviderConfigTable(
        name="anthropic-test-local",
        type=ModelProvider.ANTHROPIC,
        enabled=True,
    )
    ollama = ProviderConfigTable(
        name="ollama-test-local",
        type=ModelProvider.OLLAMA_CLOUD,
        enabled=True,
        base_url="https://ollama.example.com",
    )
    local = ProviderConfigTable(
        name="self-hosted-test",
        type=ModelProvider.LOCAL,
        enabled=True,
        base_url="http://localhost:11434",
    )
    db_session.add_all([anthropic, ollama, local])
    await db_session.flush()
    yield {"svc": ModelRoutingService(db_session), "local": local}


@pytest.mark.asyncio
async def test_derive_mode_self_hosted_when_only_local_global(
    llm_setup_with_local: dict,
) -> None:
    """Single GLOBAL assignment pointing to LOCAL → 'self_hosted' mode."""
    svc = llm_setup_with_local["svc"]
    await svc.upsert_assignment(
        scope=AssignmentScope.GLOBAL,
        scope_value=None,
        model_name="llama3.1:8b",
        provider_type_override=ModelProvider.LOCAL,
    )
    assert await svc.derive_mode() == "self_hosted"


@pytest.mark.asyncio
async def test_apply_mode_self_hosted_sets_global_local(
    llm_setup_with_local: dict,
) -> None:
    """apply_mode('self_hosted') clears assignments, enables LOCAL, inserts GLOBAL."""
    svc = llm_setup_with_local["svc"]
    await svc.apply_mode(mode="self_hosted", default_model="llama3.1:8b")
    assignments = await svc.list_assignments()
    assert len(assignments) == 1
    assert assignments[0].scope == AssignmentScope.GLOBAL
    assert assignments[0].provider.type == ModelProvider.LOCAL
    assert assignments[0].model_name == "llama3.1:8b"
    # Verify derive_mode reflects the new state.
    assert await svc.derive_mode() == "self_hosted"


@pytest.mark.asyncio
async def test_apply_mode_self_hosted_requires_default_model(
    llm_setup_with_local: dict,
) -> None:
    """apply_mode('self_hosted') without default_model raises ValueError."""
    svc = llm_setup_with_local["svc"]
    with pytest.raises(ValueError, match="requires a default_model"):
        await svc.apply_mode(mode="self_hosted")


@pytest.mark.asyncio
async def test_apply_mode_self_hosted_clears_prior_assignments(
    llm_setup_with_local: dict,
) -> None:
    """apply_mode('self_hosted') clears ALL prior assignments."""
    svc = llm_setup_with_local["svc"]
    anthropic_model = _first_model_for_type(ModelProvider.ANTHROPIC)
    await svc.upsert_assignment(
        scope=AssignmentScope.AGENT_SLUG,
        scope_value="be-dev-1",
        model_name=anthropic_model,
    )
    await svc.upsert_assignment(
        scope=AssignmentScope.GLOBAL,
        scope_value=None,
        model_name=anthropic_model,
    )
    assert len(await svc.list_assignments()) == 2  # noqa: PLR2004
    await svc.apply_mode(mode="self_hosted", default_model="gemma2:9b")
    assignments = await svc.list_assignments()
    assert len(assignments) == 1  # Only the new GLOBAL row.
    assert assignments[0].provider.type == ModelProvider.LOCAL


@pytest.mark.asyncio
async def test_upsert_assignment_routes_unknown_model_to_local(
    llm_setup_with_local: dict,
) -> None:
    """Non-catalog model names are silently routed to LOCAL if seeded."""
    svc = llm_setup_with_local["svc"]
    row = await svc.upsert_assignment(
        scope=AssignmentScope.AGENT_SLUG,
        scope_value="be-dev-1",
        model_name="my-custom-model:latest",
    )
    assert row.provider.type == ModelProvider.LOCAL
    assert row.model_name == "my-custom-model:latest"


@pytest.mark.asyncio
async def test_mix_mode_with_self_hosted_models(
    llm_setup_with_local: dict,
) -> None:
    """mix mode accepts self-hosted model names without raising ValueError."""
    svc = llm_setup_with_local["svc"]
    anthropic_model = _first_model_for_type(ModelProvider.ANTHROPIC)
    await svc.apply_mode(
        mode="mix",
        per_agent={
            "be-dev-1": anthropic_model,  # catalog model
            "be-dev-2": "self-hosted-model:7b",  # non-catalog → routed to LOCAL
        },
    )
    rows = await svc.list_assignments()
    by_slug = {r.scope_value: r for r in rows if r.scope == AssignmentScope.AGENT_SLUG}
    assert by_slug["be-dev-1"].provider.type == ModelProvider.ANTHROPIC
    assert by_slug["be-dev-2"].provider.type == ModelProvider.LOCAL
    assert by_slug["be-dev-2"].model_name == "self-hosted-model:7b"


@pytest.mark.asyncio
async def test_resolve_for_agent_self_hosted_returns_base_url(
    llm_setup_with_local: dict,
) -> None:
    """When LOCAL assignment is reachable, route has base_url from provider."""
    svc = llm_setup_with_local["svc"]
    await svc.upsert_assignment(
        scope=AssignmentScope.GLOBAL,
        scope_value=None,
        model_name="llama3.1:8b",
        provider_type_override=ModelProvider.LOCAL,
    )
    with patch(
        "roboco.services.llm.probe_ollama_tags",
        new_callable=AsyncMock,
        return_value=(["llama3.1:8b"], None),
    ):
        route = await svc.resolve_for_agent("be-dev-1")
    assert route.provider_type == ModelProvider.LOCAL
    assert route.base_url == "http://localhost:11434"
    assert route.base_url is not None


@pytest.mark.asyncio
async def test_resolve_for_agent_falls_back_when_self_hosted_unreachable(
    llm_setup_with_local: dict,
) -> None:
    """When LOCAL provider is unreachable, falls back to Anthropic default."""
    svc = llm_setup_with_local["svc"]
    await svc.upsert_assignment(
        scope=AssignmentScope.GLOBAL,
        scope_value=None,
        model_name="llama3.1:8b",
        provider_type_override=ModelProvider.LOCAL,
    )
    with patch(
        "roboco.services.llm.probe_ollama_tags",
        new_callable=AsyncMock,
        return_value=([], "Could not connect to http://localhost:11434"),
    ):
        route = await svc.resolve_for_agent("be-dev-1")
    assert route.provider_type == ModelProvider.ANTHROPIC
    assert route.base_url is None


@pytest.mark.asyncio
async def test_upsert_assignment_unknown_model_without_local_raises(
    llm_setup: dict,
) -> None:
    """Without LOCAL provider seeded, non-catalog models raise ValueError."""
    svc = llm_setup["svc"]
    with pytest.raises(ValueError, match="Unknown model"):
        await svc.upsert_assignment(
            scope=AssignmentScope.AGENT_SLUG,
            scope_value="be-dev-1",
            model_name="ghost-model:latest",
        )


@pytest.mark.asyncio
async def test_upsert_assignment_enables_local_when_disabled(
    db_session: AsyncSession,
) -> None:
    """upsert_assignment transitions LOCAL provider from enabled=False to enabled=True.

    AC4: proves that mix-mode assignment of a non-catalog model name resolves
    to provider_type LOCAL and LOCAL.enabled is True afterward — even when the
    LOCAL provider starts with enabled=False (the seeded state from migration 028
    before the operator configures a base_url via PUT /providers/self-hosted).
    """
    # Arrange: Anthropic provider (required by ModelRoutingService internals)
    # and LOCAL provider starting with enabled=False.
    anthropic = ProviderConfigTable(
        name="anthropic-ac4-test",
        type=ModelProvider.ANTHROPIC,
        enabled=True,
    )
    local = ProviderConfigTable(
        name="self-hosted-ac4-test",
        type=ModelProvider.LOCAL,
        enabled=False,  # Starts DISABLED — this is the state to be transitioned.
        base_url="http://localhost:11434",
    )
    db_session.add_all([anthropic, local])
    await db_session.flush()

    # Pre-condition: LOCAL is disabled before the call.
    assert local.enabled is False

    # Act: upsert a non-catalog model name → resolves to LOCAL →
    # calls ProviderService.update_provider(enabled=True) on the LOCAL row.
    svc = ModelRoutingService(db_session)
    row = await svc.upsert_assignment(
        scope=AssignmentScope.AGENT_SLUG,
        scope_value="test-agent-ac4",
        model_name="non-catalog-model:7b",
    )

    # Refresh local from DB so the in-memory object reflects the DB write.
    await db_session.refresh(local)

    # Assert: assignment resolved to LOCAL and LOCAL provider is now enabled.
    assert row.provider.type == ModelProvider.LOCAL
    assert row.model_name == "non-catalog-model:7b"
    assert local.enabled is True, (
        "upsert_assignment must call update_provider(enabled=True) on LOCAL "
        "whenever it routes a non-catalog model to the LOCAL provider"
    )
