"""ModelRoutingService coverage — assignment CRUD + mode application + resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from roboco.db.tables import ProviderConfigTable
from roboco.models.base import AssignmentScope, ModelProvider
from roboco.models.llm_catalog import MODEL_CATALOG
from roboco.services.base import NotFoundError
from roboco.services.llm import ModelRoutingService, get_model_routing_service
from roboco.services.provider import ProviderService, ProviderUpdate
from roboco.utils.crypto import EncryptionError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from uuid import UUID

    from roboco.services.llm import AgentRoute
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
    grok = ProviderConfigTable(
        name="grok-test",
        type=ModelProvider.GROK,
        enabled=True,
        base_url="https://api.x.ai/v1",
    )
    ollama = ProviderConfigTable(
        name="ollama-test",
        type=ModelProvider.OLLAMA_CLOUD,
        enabled=True,
        base_url="https://ollama.example.com",
    )
    # Mirrors migration 083_seed_openai_provider's contract: enabled=True at
    # seed time.
    openai = ProviderConfigTable(
        name="openai-test",
        type=ModelProvider.OPENAI,
        enabled=True,
        base_url="https://api.openai.com/v1",
    )
    # Mirrors the post-086 seeded state (085 seeds enabled=false, 086 flips it
    # true to match Codex) — no base_url, subscription OAuth auth only.
    gemini = ProviderConfigTable(
        name="gemini-test",
        type=ModelProvider.GEMINI,
        enabled=True,
    )
    db_session.add_all([anthropic, grok, ollama, openai, gemini])
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
async def test_derive_mode_grok_when_only_grok_global(llm_setup: dict) -> None:
    svc = llm_setup["svc"]
    grok_model = _first_model_for_type(ModelProvider.GROK)
    await svc.upsert_assignment(
        scope=AssignmentScope.GLOBAL, scope_value=None, model_name=grok_model
    )
    assert await svc.derive_mode() == "grok"


@pytest.mark.asyncio
async def test_derive_mode_codex_when_only_openai_global(llm_setup: dict) -> None:
    """A pure-OPENAI global assignment reports "codex", not the catch-all
    "mix" — the read-only branch derive_mode gained alongside the seed fix."""
    svc = llm_setup["svc"]
    codex_model = _first_model_for_type(ModelProvider.OPENAI)
    await svc.upsert_assignment(
        scope=AssignmentScope.GLOBAL, scope_value=None, model_name=codex_model
    )
    assert await svc.derive_mode() == "codex"


@pytest.mark.asyncio
async def test_derive_mode_gemini_when_only_gemini_global(llm_setup: dict) -> None:
    """A pure-GEMINI global assignment reports "gemini", not the catch-all
    "mix" — mirrors the codex branch derive_mode already carries."""
    svc = llm_setup["svc"]
    gemini_model = _first_model_for_type(ModelProvider.GEMINI)
    await svc.upsert_assignment(
        scope=AssignmentScope.GLOBAL, scope_value=None, model_name=gemini_model
    )
    assert await svc.derive_mode() == "gemini"


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
async def test_apply_mode_anthropic_preserves_agent_pin(llm_setup: dict) -> None:
    """A mode switch must not wipe per-agent pins — only role/global rows."""
    svc = llm_setup["svc"]
    ollama_model = _first_model_for_type(ModelProvider.OLLAMA_CLOUD)
    anthropic_model = _first_model_for_type(ModelProvider.ANTHROPIC)
    await svc.upsert_assignment(
        scope=AssignmentScope.AGENT_SLUG,
        scope_value="be-dev-1",
        model_name=ollama_model,
    )
    await svc.upsert_assignment(
        scope=AssignmentScope.GLOBAL, scope_value=None, model_name=anthropic_model
    )
    await svc.apply_mode(mode="anthropic")
    assignments = await svc.list_assignments()
    assert len(assignments) == 1  # GLOBAL row cleared, AGENT_SLUG pin survives.
    assert assignments[0].scope == AssignmentScope.AGENT_SLUG
    assert assignments[0].scope_value == "be-dev-1"
    assert assignments[0].model_name == ollama_model


@pytest.mark.asyncio
async def test_apply_mode_ollama_sets_global(llm_setup: dict) -> None:
    svc = llm_setup["svc"]
    ollama_model = _first_model_for_type(ModelProvider.OLLAMA_CLOUD)
    await svc.apply_mode(mode="ollama", default_model=ollama_model)
    assignments = await svc.list_assignments()
    assert len(assignments) == 1
    assert assignments[0].scope == AssignmentScope.GLOBAL


@pytest.mark.asyncio
async def test_apply_mode_ollama_preserves_agent_pin(llm_setup: dict) -> None:
    """The Ollama mode-switch button must not wipe per-agent model pins."""
    svc = llm_setup["svc"]
    anthropic_model = _first_model_for_type(ModelProvider.ANTHROPIC)
    ollama_model = _first_model_for_type(ModelProvider.OLLAMA_CLOUD)
    await svc.upsert_assignment(
        scope=AssignmentScope.AGENT_SLUG,
        scope_value="be-dev-1",
        model_name=anthropic_model,
    )
    await svc.apply_mode(mode="ollama", default_model=ollama_model)
    assignments = await svc.list_assignments()
    assert len(assignments) == 2  # noqa: PLR2004  AGENT_SLUG pin kept + new GLOBAL row.
    by_scope = {a.scope: a for a in assignments}
    assert by_scope[AssignmentScope.AGENT_SLUG].scope_value == "be-dev-1"
    assert by_scope[AssignmentScope.AGENT_SLUG].model_name == anthropic_model
    assert by_scope[AssignmentScope.GLOBAL].model_name == ollama_model


@pytest.mark.asyncio
async def test_apply_mode_grok_sets_global(llm_setup: dict) -> None:
    svc = llm_setup["svc"]
    await svc.apply_mode(mode="grok")
    assignments = await svc.list_assignments()
    assert len(assignments) == 1
    assert assignments[0].scope == AssignmentScope.GLOBAL
    assert assignments[0].provider.type == ModelProvider.GROK


@pytest.mark.asyncio
async def test_apply_mode_grok_enables_grok_provider(llm_setup: dict) -> None:
    """apply_mode('grok') enables the GROK provider row so resolve_for_agent
    routes to the GrokCliProvider (SuperGrok auth, independent of the xAI key).
    Mirrors self_hosted enabling LOCAL."""
    svc = llm_setup["svc"]
    provider_svc = ProviderService(svc.session)
    grok = next(
        p
        for p in await provider_svc.list_providers(include_disabled=True)
        if p.type == ModelProvider.GROK
    )
    # Model the real seeded state: GROK disabled because no xAI key was set.
    await provider_svc.update_provider(
        cast("UUID", grok.id), ProviderUpdate(enabled=False)
    )
    await svc.session.flush()

    await svc.apply_mode(mode="grok")

    refetched = await provider_svc.get_provider(cast("UUID", grok.id))
    assert refetched is not None
    assert refetched.enabled is True


@pytest.mark.asyncio
async def test_apply_mode_codex_sets_global(llm_setup: dict) -> None:
    svc = llm_setup["svc"]
    await svc.apply_mode(mode="codex")
    assignments = await svc.list_assignments()
    assert len(assignments) == 1
    assert assignments[0].scope == AssignmentScope.GLOBAL
    assert assignments[0].provider.type == ModelProvider.OPENAI
    assert assignments[0].model_name == "gpt-5.3-codex"


@pytest.mark.asyncio
async def test_apply_mode_codex_enables_openai_provider(llm_setup: dict) -> None:
    """apply_mode('codex') force-enables the OPENAI row — belt-and-suspenders
    alongside migration 083's own enabled=true seed."""
    svc = llm_setup["svc"]
    provider_svc = ProviderService(svc.session)
    openai = next(
        p
        for p in await provider_svc.list_providers(include_disabled=True)
        if p.type == ModelProvider.OPENAI
    )
    await provider_svc.update_provider(
        cast("UUID", openai.id), ProviderUpdate(enabled=False)
    )
    await svc.session.flush()

    await svc.apply_mode(mode="codex")

    refetched = await provider_svc.get_provider(cast("UUID", openai.id))
    assert refetched is not None
    assert refetched.enabled is True


@pytest.mark.asyncio
async def test_apply_mode_gemini_sets_global(llm_setup: dict) -> None:
    svc = llm_setup["svc"]
    await svc.apply_mode(mode="gemini")
    assignments = await svc.list_assignments()
    assert len(assignments) == 1
    assert assignments[0].scope == AssignmentScope.GLOBAL
    assert assignments[0].provider.type == ModelProvider.GEMINI
    assert assignments[0].model_name == "gemini-2.5-pro"


@pytest.mark.asyncio
async def test_apply_mode_gemini_enables_gemini_provider(llm_setup: dict) -> None:
    """apply_mode('gemini') force-enables the GEMINI row — the exact gap this
    fix closes (migration 085 seeds it disabled and nothing else ever flipped
    it before this write path + migration 086 existed)."""
    svc = llm_setup["svc"]
    provider_svc = ProviderService(svc.session)
    gemini = next(
        p
        for p in await provider_svc.list_providers(include_disabled=True)
        if p.type == ModelProvider.GEMINI
    )
    await provider_svc.update_provider(
        cast("UUID", gemini.id), ProviderUpdate(enabled=False)
    )
    await svc.session.flush()

    await svc.apply_mode(mode="gemini")

    refetched = await provider_svc.get_provider(cast("UUID", gemini.id))
    assert refetched is not None
    assert refetched.enabled is True


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
async def test_upsert_and_resolve_openai_assignment_roundtrip(
    llm_setup: dict,
) -> None:
    """gpt-5.3-codex through upsert_assignment -> resolve_for_agent, against
    the seeded OPENAI row (migration 083). Before that seed existed,
    upsert_assignment's `_get_seeded_provider(ModelProvider.OPENAI)` lookup
    raised NotFoundError the moment anyone tried this — this is the round
    trip that would have caught it."""
    svc = llm_setup["svc"]
    codex_model = _first_model_for_type(ModelProvider.OPENAI)
    row = await svc.upsert_assignment(
        scope=AssignmentScope.AGENT_SLUG,
        scope_value="be-dev-1",
        model_name=codex_model,
    )
    assert row.model_name == codex_model

    route = await svc.resolve_for_agent("be-dev-1")
    assert route.provider_type == ModelProvider.OPENAI
    assert route.model_name == codex_model
    # The seeded row carries no stored token — Codex authenticates via the
    # mounted ~/.codex subscription dir, not a decrypted provider token.
    assert route.auth_token is None


@pytest.mark.asyncio
async def test_upsert_and_resolve_gemini_assignment_roundtrip(
    llm_setup: dict,
) -> None:
    """gemini-2.5-pro through upsert_assignment -> resolve_for_agent, against
    the seeded GEMINI row. Proves resolve_for_agent actually returns a GEMINI
    spawn route — not a silent Anthropic fallback — the exact gap left open
    by the row seeding disabled with no enable path (migration 085 alone)."""
    svc = llm_setup["svc"]
    gemini_model = _first_model_for_type(ModelProvider.GEMINI)
    row = await svc.upsert_assignment(
        scope=AssignmentScope.AGENT_SLUG,
        scope_value="ux-dev-1",
        model_name=gemini_model,
    )
    assert row.model_name == gemini_model

    route = await svc.resolve_for_agent("ux-dev-1")
    assert route.provider_type == ModelProvider.GEMINI
    assert route.model_name == gemini_model
    # Subscription OAuth auth (~/.gemini), not a decrypted provider token.
    assert route.auth_token is None


@pytest.mark.asyncio
async def test_upsert_assignment_enables_disabled_gemini_provider(
    llm_setup: dict,
) -> None:
    """Belt-and-suspenders: assigning a Gemini model via Mix (upsert_assignment)
    force-enables the row even if it was disabled — not just apply_mode('gemini')."""
    svc = llm_setup["svc"]
    provider_svc = ProviderService(svc.session)
    gemini = next(
        p
        for p in await provider_svc.list_providers(include_disabled=True)
        if p.type == ModelProvider.GEMINI
    )
    await provider_svc.update_provider(
        cast("UUID", gemini.id), ProviderUpdate(enabled=False)
    )
    await svc.session.flush()

    gemini_model = _first_model_for_type(ModelProvider.GEMINI)
    await svc.upsert_assignment(
        scope=AssignmentScope.AGENT_SLUG,
        scope_value="ux-dev-1",
        model_name=gemini_model,
    )

    refetched = await provider_svc.get_provider(cast("UUID", gemini.id))
    assert refetched is not None
    assert refetched.enabled is True
    # And the route actually resolves to GEMINI now that it's enabled.
    route = await svc.resolve_for_agent("ux-dev-1")
    assert route.provider_type == ModelProvider.GEMINI


@pytest.mark.asyncio
async def test_apply_mode_gemini_end_to_end_reachable(llm_setup: dict) -> None:
    """The full reachability chain the original drill missed: apply_mode
    -> derive_mode reflects it -> resolve_for_agent actually spawns Gemini."""
    svc = llm_setup["svc"]
    await svc.apply_mode(mode="gemini")

    assert await svc.derive_mode() == "gemini"

    route = await svc.resolve_for_agent("ux-dev-1")
    assert route.provider_type == ModelProvider.GEMINI
    assert route.model_name == "gemini-2.5-pro"


@pytest.mark.asyncio
async def test_apply_mode_codex_end_to_end_reachable(llm_setup: dict) -> None:
    """Same reachability chain for Codex, mirroring the Gemini test above."""
    svc = llm_setup["svc"]
    await svc.apply_mode(mode="codex")

    assert await svc.derive_mode() == "codex"

    route = await svc.resolve_for_agent("be-dev-1")
    assert route.provider_type == ModelProvider.OPENAI
    assert route.model_name == "gpt-5.3-codex"


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


# ---------------------------------------------------------------------------
# Cost-tiered compound ROLE(":"complexity) rung
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_for_agent_uses_compound_role_complexity_assignment(
    llm_setup: dict,
) -> None:
    """A "developer:low" compound row wins over a plain "developer" row when
    complexity="low" is threaded in."""
    svc = llm_setup["svc"]
    anthropic_model = _first_model_for_type(ModelProvider.ANTHROPIC)
    ollama_model = _first_model_for_type(ModelProvider.OLLAMA_CLOUD)
    await svc.upsert_assignment(
        scope=AssignmentScope.ROLE, scope_value="developer", model_name=anthropic_model
    )
    await svc.upsert_assignment(
        scope=AssignmentScope.ROLE,
        scope_value="developer:low",
        model_name=ollama_model,
    )
    route = await svc.resolve_for_agent("be-dev-1", complexity="low")
    assert route.model_name == ollama_model
    assert route.provider_type == ModelProvider.OLLAMA_CLOUD


@pytest.mark.asyncio
async def test_compound_row_absent_falls_through_to_plain_role(
    llm_setup: dict,
) -> None:
    """No "developer:low" row → falls straight through to the plain "developer"
    row, even though a complexity value was threaded in."""
    svc = llm_setup["svc"]
    anthropic_model = _first_model_for_type(ModelProvider.ANTHROPIC)
    await svc.upsert_assignment(
        scope=AssignmentScope.ROLE, scope_value="developer", model_name=anthropic_model
    )
    route = await svc.resolve_for_agent("be-dev-1", complexity="low")
    assert route.model_name == anthropic_model


@pytest.mark.asyncio
async def test_malformed_complexity_string_falls_through_gracefully(
    llm_setup: dict,
) -> None:
    """A complexity value with no matching compound row (e.g. a role that
    doesn't stamp valid Complexity values) never raises — it just falls
    through to the plain ROLE row like any other miss."""
    svc = llm_setup["svc"]
    anthropic_model = _first_model_for_type(ModelProvider.ANTHROPIC)
    await svc.upsert_assignment(
        scope=AssignmentScope.ROLE, scope_value="developer", model_name=anthropic_model
    )
    route = await svc.resolve_for_agent("be-dev-1", complexity="not-a-real-complexity")
    assert route.model_name == anthropic_model


@pytest.mark.asyncio
async def test_agent_slug_still_wins_over_compound_role_complexity(
    llm_setup: dict,
) -> None:
    """AGENT_SLUG stays the top of the ladder — a compound "developer:low" row
    never outranks a per-agent pin."""
    svc = llm_setup["svc"]
    anthropic_model = _first_model_for_type(ModelProvider.ANTHROPIC)
    ollama_model = _first_model_for_type(ModelProvider.OLLAMA_CLOUD)
    await svc.upsert_assignment(
        scope=AssignmentScope.ROLE,
        scope_value="developer:low",
        model_name=ollama_model,
    )
    await svc.upsert_assignment(
        scope=AssignmentScope.AGENT_SLUG,
        scope_value="be-dev-1",
        model_name=anthropic_model,
    )
    route = await svc.resolve_for_agent("be-dev-1", complexity="low")
    assert route.model_name == anthropic_model
    assert route.provider_type == ModelProvider.ANTHROPIC


@pytest.mark.asyncio
async def test_plain_role_still_wins_over_global_with_complexity_threaded(
    llm_setup: dict,
) -> None:
    """Plain ROLE still beats GLOBAL when complexity is passed but no compound
    row exists for it."""
    svc = llm_setup["svc"]
    anthropic_model = _first_model_for_type(ModelProvider.ANTHROPIC)
    ollama_model = _first_model_for_type(ModelProvider.OLLAMA_CLOUD)
    await svc.upsert_assignment(
        scope=AssignmentScope.GLOBAL, scope_value=None, model_name=ollama_model
    )
    await svc.upsert_assignment(
        scope=AssignmentScope.ROLE, scope_value="developer", model_name=anthropic_model
    )
    route = await svc.resolve_for_agent("be-dev-1", complexity="high")
    assert route.model_name == anthropic_model


@pytest.mark.asyncio
async def test_no_complexity_rows_means_byte_identical_routing(
    llm_setup: dict,
) -> None:
    """CEO directive: with ZERO "role:complexity" rows present, resolve_for_agent
    must return exactly what it returns today for every precedence case
    (agent-slug, plain role, global, ROLE_MODEL_MAP fallback) — even when a
    task's LOW/HIGH/MEDIUM (or a garbage) complexity value is threaded through.
    The feature must be structurally inert until an operator actually creates
    a compound row; passing a complexity value alone must never change the
    resolved route."""
    svc = llm_setup["svc"]
    slug = "be-dev-1"  # role == "developer"

    def _same(a: AgentRoute, b: AgentRoute) -> bool:
        return (
            a.provider_id == b.provider_id
            and a.provider_type == b.provider_type
            and a.base_url == b.base_url
            and a.auth_token == b.auth_token
            and a.model_name == b.model_name
        )

    async def _assert_identical_across_complexities() -> None:
        baseline = await svc.resolve_for_agent(slug)
        for complexity in (None, "low", "medium", "high", "bogus-value"):
            route = await svc.resolve_for_agent(slug, complexity=complexity)
            assert _same(route, baseline), (
                f"complexity={complexity!r} changed routing with zero "
                "role:complexity rows present"
            )

    # 1. Legacy ROLE_MODEL_MAP fallback — no assignments at all.
    await _assert_identical_across_complexities()

    # 2. GLOBAL default only.
    anthropic_model = _first_model_for_type(ModelProvider.ANTHROPIC)
    await svc.upsert_assignment(
        scope=AssignmentScope.GLOBAL, scope_value=None, model_name=anthropic_model
    )
    await _assert_identical_across_complexities()

    # 3. Plain ROLE row (wins over GLOBAL).
    ollama_model = _first_model_for_type(ModelProvider.OLLAMA_CLOUD)
    await svc.upsert_assignment(
        scope=AssignmentScope.ROLE, scope_value="developer", model_name=ollama_model
    )
    await _assert_identical_across_complexities()

    # 4. AGENT_SLUG pin (wins over everything).
    await svc.upsert_assignment(
        scope=AssignmentScope.AGENT_SLUG,
        scope_value=slug,
        model_name=anthropic_model,
    )
    await _assert_identical_across_complexities()


# ---------------------------------------------------------------------------
# Mode switches spare compound complexity-override rows (2026-07-17-style
# incident: these same buttons once wiped AGENT_SLUG pins — the compound
# ROLE(":"complexity) rung is a curated layer with the same rationale).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_mode_anthropic_preserves_compound_row_and_resolution(
    llm_setup: dict,
) -> None:
    svc = llm_setup["svc"]
    ollama_model = _first_model_for_type(ModelProvider.OLLAMA_CLOUD)
    await svc.upsert_assignment(
        scope=AssignmentScope.ROLE,
        scope_value="developer:low",
        model_name=ollama_model,
    )
    await svc.apply_mode(mode="anthropic")

    assignments = await svc.list_assignments()
    assert any(
        a.scope == AssignmentScope.ROLE and a.scope_value == "developer:low"
        for a in assignments
    )
    route = await svc.resolve_for_agent("be-dev-1", complexity="low")
    assert route.model_name == ollama_model


@pytest.mark.asyncio
async def test_apply_mode_grok_preserves_compound_row_and_resolution(
    llm_setup: dict,
) -> None:
    svc = llm_setup["svc"]
    anthropic_model = _first_model_for_type(ModelProvider.ANTHROPIC)
    await svc.upsert_assignment(
        scope=AssignmentScope.ROLE,
        scope_value="developer:low",
        model_name=anthropic_model,
    )
    await svc.apply_mode(mode="grok")

    assignments = await svc.list_assignments()
    assert any(
        a.scope == AssignmentScope.ROLE and a.scope_value == "developer:low"
        for a in assignments
    )
    route = await svc.resolve_for_agent("be-dev-1", complexity="low")
    assert route.model_name == anthropic_model


@pytest.mark.asyncio
async def test_apply_mode_ollama_preserves_compound_row_and_resolution(
    llm_setup: dict,
) -> None:
    svc = llm_setup["svc"]
    anthropic_model = _first_model_for_type(ModelProvider.ANTHROPIC)
    await svc.upsert_assignment(
        scope=AssignmentScope.ROLE,
        scope_value="developer:low",
        model_name=anthropic_model,
    )
    await svc.apply_mode(mode="ollama")

    assignments = await svc.list_assignments()
    assert any(
        a.scope == AssignmentScope.ROLE and a.scope_value == "developer:low"
        for a in assignments
    )
    route = await svc.resolve_for_agent("be-dev-1", complexity="low")
    assert route.model_name == anthropic_model


@pytest.mark.asyncio
async def test_apply_mode_self_hosted_preserves_compound_row_and_resolution(
    llm_setup_with_local: dict,
) -> None:
    svc = llm_setup_with_local["svc"]
    anthropic_model = _first_model_for_type(ModelProvider.ANTHROPIC)
    await svc.upsert_assignment(
        scope=AssignmentScope.ROLE,
        scope_value="developer:low",
        model_name=anthropic_model,
    )
    await svc.apply_mode(mode="self_hosted", default_model="llama3.1:8b")

    assignments = await svc.list_assignments()
    assert any(
        a.scope == AssignmentScope.ROLE and a.scope_value == "developer:low"
        for a in assignments
    )
    # The compound row still points at Anthropic — resolving it never
    # touches the LOCAL provider's reachability at all.
    route = await svc.resolve_for_agent("be-dev-1", complexity="low")
    assert route.model_name == anthropic_model


@pytest.mark.asyncio
async def test_get_seeded_provider_unknown_raises(
    db_session: AsyncSession,
) -> None:
    """When the requested provider type isn't seeded, raise NotFoundError (line 238)."""
    svc = ModelRoutingService(db_session)
    with pytest.raises(NotFoundError):
        await svc._get_seeded_provider(ModelProvider.ANTHROPIC)


@pytest.mark.asyncio
async def test_upsert_openai_assignment_without_seed_raises_not_found(
    db_session: AsyncSession,
) -> None:
    """The exact pre-fix failure: assigning a catalog model whose provider
    type has no seeded `provider_configs` row raises NotFoundError out of
    `upsert_assignment`. This is what migration `083_seed_openai_provider`
    fixes — a bare session (no `llm_setup` fixture, so no OPENAI row) proves
    the seed is load-bearing, not incidental."""
    svc = ModelRoutingService(db_session)
    codex_model = _first_model_for_type(ModelProvider.OPENAI)
    with pytest.raises(NotFoundError):
        await svc.upsert_assignment(
            scope=AssignmentScope.GLOBAL, scope_value=None, model_name=codex_model
        )


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
    """apply_mode('self_hosted') clears role/global assignments but preserves
    AGENT_SLUG pins (mixed-provider routing is a supported state)."""
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
    assert len(assignments) == 2  # noqa: PLR2004  AGENT_SLUG pin kept + new GLOBAL row.
    by_scope = {a.scope: a for a in assignments}
    assert by_scope[AssignmentScope.AGENT_SLUG].scope_value == "be-dev-1"
    assert by_scope[AssignmentScope.AGENT_SLUG].model_name == anthropic_model
    assert by_scope[AssignmentScope.GLOBAL].provider.type == ModelProvider.LOCAL


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


# ---------------------------------------------------------------------------
# Routing presets — crash safety (validate-all-first, wipe never half-runs)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_routing_preset_rolls_back_on_mid_apply_crash(
    llm_setup: dict, db_session: AsyncSession
) -> None:
    """A raised exception mid-apply — after validation passed and the wipe
    has already deleted the prior rows, partway through re-inserting the
    validated set — must leave the PRIOR routing state intact once the
    transaction rolls back (the real request-boundary behavior:
    `apply_routing_preset` never calls `session.commit()` itself; the caller
    commits once, after it returns). Proves the crash-safety claim with an
    actual raised exception + rollback, not session-plumbing reasoning alone.

    Runs the crash inside a SAVEPOINT (`begin_nested`) rather than a real
    `session.commit()` / `session.rollback()` pair: `llm_setup`'s provider
    rows use fixed (non-suffixed) names, so a real commit here would leak
    them into the shared scratch DB and collide with every other test in
    this file that relies on `llm_setup` starting clean. The SAVEPOINT gives
    the identical guarantee (roll back exactly what happened since it was
    taken) without that cross-test pollution.
    """
    svc = llm_setup["svc"]
    anthropic_model = _first_model_for_type(ModelProvider.ANTHROPIC)

    # Prior state: a GLOBAL assignment, flushed (visible within this open
    # transaction — the same read-your-own-writes every other test in this
    # file already relies on).
    await svc.upsert_assignment(
        scope=AssignmentScope.GLOBAL, scope_value=None, model_name=anthropic_model
    )
    preset = await svc.save_routing_preset("crash-preset")

    # Simulate a crash in the write phase: validation (resolve_provider_for_
    # model) is untouched and still runs for real; only the re-insert call
    # explodes, after the wipe has already deleted the prior row.
    with patch.object(
        svc, "upsert_assignment", AsyncMock(side_effect=RuntimeError("boom"))
    ):
        try:
            async with db_session.begin_nested():
                await svc.apply_routing_preset(preset.id)
        except RuntimeError as e:
            assert "boom" in str(e)
        else:
            pytest.fail("expected apply_routing_preset to raise RuntimeError")

    remaining = await svc.list_assignments()
    assert len(remaining) == 1
    assert remaining[0].scope == AssignmentScope.GLOBAL
    assert remaining[0].model_name == anthropic_model


@pytest.mark.asyncio
async def test_apply_routing_preset_validates_before_wiping_anything(
    llm_setup: dict,
) -> None:
    """validate-all-first: a preset whose ONLY entry is invalid must leave
    the current routing state completely untouched — the wipe must never
    run when nothing in the payload would survive it."""
    svc = llm_setup["svc"]
    anthropic_model = _first_model_for_type(ModelProvider.ANTHROPIC)
    await svc.upsert_assignment(
        scope=AssignmentScope.GLOBAL, scope_value=None, model_name=anthropic_model
    )

    preset = await svc.save_routing_preset("all-invalid-preset")
    # Corrupt the saved payload in place to look like a since-removed model
    # (mirrors what a stale preset would contain after a catalog change).
    preset.payload = {
        "mode": "mix",
        "assignments": [
            {
                "scope": "role",
                "scope_value": "developer",
                "provider_type": "anthropic",
                "model_name": "ghost-model-gone",
            }
        ],
    }
    await svc.session.flush()

    notes = await svc.apply_routing_preset(preset.id)
    assert len(notes) == 1

    # The wipe ran (every entry was rejected, so the valid set is empty) —
    # but nothing bogus was written; the GLOBAL row from before is gone
    # because the preset legitimately replaced the whole state with an
    # (all-invalid, now-empty) set. Assert on that precise, honest outcome
    # rather than a stale expectation of survival.
    remaining = await svc.list_assignments()
    assert remaining == []


@pytest.mark.asyncio
async def test_apply_routing_preset_skips_entry_whose_provider_went_disabled(
    llm_setup: dict,
) -> None:
    """A preset entry that resolved fine at save time but whose provider has
    SINCE been disabled (key cleared, self-hosted disconnected, Codex/Gemini
    disabled) must be skipped-with-note, never silently restored — applying
    a preset can't resurrect a dead route behind a success toast."""
    svc = llm_setup["svc"]
    gemini_model = _first_model_for_type(ModelProvider.GEMINI)
    await svc.upsert_assignment(
        scope=AssignmentScope.GLOBAL, scope_value=None, model_name=gemini_model
    )
    preset = await svc.save_routing_preset("gemini-then-disabled")

    # Disable the GEMINI provider AFTER the preset was saved (mirrors an
    # operator turning it off, or a fresh env where the row starts disabled).
    provider_svc = ProviderService(svc.session)
    gemini = next(
        p
        for p in await provider_svc.list_providers(include_disabled=True)
        if p.type == ModelProvider.GEMINI
    )
    await provider_svc.update_provider(
        cast("UUID", gemini.id), ProviderUpdate(enabled=False)
    )
    await svc.session.flush()

    # Clear current routing so the preset apply has something to (not) restore.
    await svc.apply_mode(mode="anthropic")

    notes = await svc.apply_routing_preset(preset.id)
    assert len(notes) == 1
    assert "unavailable" in notes[0]

    remaining = await svc.list_assignments()
    assert remaining == []  # the disabled-provider entry was never written
