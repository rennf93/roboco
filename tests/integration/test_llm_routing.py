"""ModelRoutingService coverage — assignment CRUD + mode application + resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from roboco.db.tables import ProviderConfigTable
from roboco.models.base import AssignmentScope, ModelProvider
from roboco.models.llm_catalog import MODEL_CATALOG
from roboco.services.base import NotFoundError
from roboco.services.llm import ModelRoutingService

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
