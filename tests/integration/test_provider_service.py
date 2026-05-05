"""ProviderService coverage — list/get/create/update/delete/decrypt.

Drives a real `db_session` via the project's Postgres-backed conftest.
Provider rows are encrypted at rest with Fernet; tests round-trip
plaintext → ciphertext → plaintext through `get_decrypted_token` and
exercise the tri-state semantics of ``ProviderUpdate.auth_token``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import ModelAssignmentTable
from roboco.models.base import ModelProvider
from roboco.services.base import ConflictError, NotFoundError
from roboco.services.provider import (
    ProviderCreate,
    ProviderService,
    ProviderUpdate,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def provider_svc(db_session: AsyncSession) -> AsyncIterator[ProviderService]:
    yield ProviderService(db_session)


@pytest.mark.asyncio
async def test_create_provider_with_token_encrypts(
    provider_svc: ProviderService,
) -> None:
    row = await provider_svc.create_provider(
        ProviderCreate(
            name=f"anthropic-{uuid4().hex[:6]}",
            type=ModelProvider.ANTHROPIC,
            auth_token="sk-test-secret",
        )
    )
    assert row.auth_token_encrypted is not None
    assert row.auth_token_encrypted != "sk-test-secret"


@pytest.mark.asyncio
async def test_create_provider_without_token_leaves_null(
    provider_svc: ProviderService,
) -> None:
    row = await provider_svc.create_provider(
        ProviderCreate(name=f"p-{uuid4().hex[:6]}", type=ModelProvider.LOCAL)
    )
    assert row.auth_token_encrypted is None


@pytest.mark.asyncio
async def test_create_provider_duplicate_name_raises(
    provider_svc: ProviderService,
) -> None:
    name = f"dup-{uuid4().hex[:6]}"
    await provider_svc.create_provider(
        ProviderCreate(name=name, type=ModelProvider.ANTHROPIC)
    )
    with pytest.raises(ConflictError):
        await provider_svc.create_provider(
            ProviderCreate(name=name, type=ModelProvider.OPENAI)
        )


@pytest.mark.asyncio
async def test_get_provider_returns_row(provider_svc: ProviderService) -> None:
    row = await provider_svc.create_provider(
        ProviderCreate(name=f"g-{uuid4().hex[:6]}", type=ModelProvider.OPENAI)
    )
    fetched = await provider_svc.get_provider(row.id)
    assert fetched is not None
    assert fetched.id == row.id


@pytest.mark.asyncio
async def test_get_provider_returns_none_when_missing(
    provider_svc: ProviderService,
) -> None:
    assert await provider_svc.get_provider(uuid4()) is None


@pytest.mark.asyncio
async def test_get_provider_or_raise_raises(provider_svc: ProviderService) -> None:
    with pytest.raises(NotFoundError):
        await provider_svc.get_provider_or_raise(uuid4())


@pytest.mark.asyncio
async def test_get_by_name(provider_svc: ProviderService) -> None:
    name = f"by-name-{uuid4().hex[:6]}"
    row = await provider_svc.create_provider(
        ProviderCreate(name=name, type=ModelProvider.ANTHROPIC)
    )
    found = await provider_svc.get_by_name(name)
    assert found is not None
    assert found.id == row.id


@pytest.mark.asyncio
async def test_list_providers_excludes_disabled_by_default(
    provider_svc: ProviderService,
) -> None:
    enabled = await provider_svc.create_provider(
        ProviderCreate(
            name=f"on-{uuid4().hex[:6]}", type=ModelProvider.ANTHROPIC, enabled=True
        )
    )
    disabled = await provider_svc.create_provider(
        ProviderCreate(
            name=f"off-{uuid4().hex[:6]}", type=ModelProvider.LOCAL, enabled=False
        )
    )
    visible = await provider_svc.list_providers()
    visible_ids = {p.id for p in visible}
    assert enabled.id in visible_ids
    assert disabled.id not in visible_ids


@pytest.mark.asyncio
async def test_list_providers_include_disabled(provider_svc: ProviderService) -> None:
    disabled = await provider_svc.create_provider(
        ProviderCreate(
            name=f"x-{uuid4().hex[:6]}", type=ModelProvider.LOCAL, enabled=False
        )
    )
    every = await provider_svc.list_providers(include_disabled=True)
    assert disabled.id in {p.id for p in every}


@pytest.mark.asyncio
async def test_update_provider_changes_name(provider_svc: ProviderService) -> None:
    row = await provider_svc.create_provider(
        ProviderCreate(name=f"old-{uuid4().hex[:6]}", type=ModelProvider.OPENAI)
    )
    new_name = f"new-{uuid4().hex[:6]}"
    updated = await provider_svc.update_provider(row.id, ProviderUpdate(name=new_name))
    assert updated is not None
    assert updated.name == new_name


@pytest.mark.asyncio
async def test_update_provider_duplicate_name_raises(
    provider_svc: ProviderService,
) -> None:
    a = await provider_svc.create_provider(
        ProviderCreate(name=f"a-{uuid4().hex[:6]}", type=ModelProvider.OPENAI)
    )
    b = await provider_svc.create_provider(
        ProviderCreate(name=f"b-{uuid4().hex[:6]}", type=ModelProvider.OPENAI)
    )
    with pytest.raises(ConflictError):
        await provider_svc.update_provider(b.id, ProviderUpdate(name=a.name))


@pytest.mark.asyncio
async def test_update_provider_clears_base_url_with_empty_string(
    provider_svc: ProviderService,
) -> None:
    row = await provider_svc.create_provider(
        ProviderCreate(
            name=f"url-{uuid4().hex[:6]}",
            type=ModelProvider.OLLAMA_CLOUD,
            base_url="https://example.com",
        )
    )
    updated = await provider_svc.update_provider(row.id, ProviderUpdate(base_url=""))
    assert updated is not None
    assert updated.base_url is None


@pytest.mark.asyncio
async def test_update_provider_token_tristate_clear(
    provider_svc: ProviderService,
) -> None:
    row = await provider_svc.create_provider(
        ProviderCreate(
            name=f"t-{uuid4().hex[:6]}",
            type=ModelProvider.OLLAMA_CLOUD,
            auth_token="initial",
        )
    )
    updated = await provider_svc.update_provider(
        row.id, ProviderUpdate(clear_auth_token=True)
    )
    assert updated is not None
    assert updated.auth_token_encrypted is None


@pytest.mark.asyncio
async def test_update_provider_token_tristate_set(
    provider_svc: ProviderService,
) -> None:
    row = await provider_svc.create_provider(
        ProviderCreate(name=f"s-{uuid4().hex[:6]}", type=ModelProvider.ANTHROPIC)
    )
    updated = await provider_svc.update_provider(
        row.id, ProviderUpdate(auth_token="new-secret")
    )
    assert updated is not None
    assert updated.auth_token_encrypted is not None


@pytest.mark.asyncio
async def test_update_provider_token_tristate_unchanged(
    provider_svc: ProviderService,
) -> None:
    row = await provider_svc.create_provider(
        ProviderCreate(
            name=f"u-{uuid4().hex[:6]}",
            type=ModelProvider.ANTHROPIC,
            auth_token="initial",
        )
    )
    original_token = row.auth_token_encrypted
    updated = await provider_svc.update_provider(
        row.id,
        ProviderUpdate(enabled=False),  # no auth_token field
    )
    assert updated is not None
    assert updated.auth_token_encrypted == original_token  # unchanged
    assert updated.enabled is False


@pytest.mark.asyncio
async def test_update_provider_returns_none_for_missing(
    provider_svc: ProviderService,
) -> None:
    assert (
        await provider_svc.update_provider(uuid4(), ProviderUpdate(enabled=False))
        is None
    )


@pytest.mark.asyncio
async def test_delete_provider(provider_svc: ProviderService) -> None:
    row = await provider_svc.create_provider(
        ProviderCreate(name=f"d-{uuid4().hex[:6]}", type=ModelProvider.OPENAI)
    )
    await provider_svc.delete_provider(row.id)
    assert await provider_svc.get_provider(row.id) is None


@pytest.mark.asyncio
async def test_delete_provider_raises_when_referenced(
    db_session: AsyncSession, provider_svc: ProviderService
) -> None:
    row = await provider_svc.create_provider(
        ProviderCreate(name=f"r-{uuid4().hex[:6]}", type=ModelProvider.OPENAI)
    )
    # Insert a model assignment that references this provider.
    assignment = ModelAssignmentTable(
        id=uuid4(),
        scope="role",
        scope_value="developer",
        provider_config_id=row.id,
        model_name="claude-haiku-4-5",
    )
    db_session.add(assignment)
    await db_session.flush()

    with pytest.raises(ConflictError):
        await provider_svc.delete_provider(row.id)


@pytest.mark.asyncio
async def test_get_decrypted_token_round_trip(provider_svc: ProviderService) -> None:
    plaintext = "sk-roundtrip-secret"
    row = await provider_svc.create_provider(
        ProviderCreate(
            name=f"rt-{uuid4().hex[:6]}",
            type=ModelProvider.ANTHROPIC,
            auth_token=plaintext,
        )
    )
    decrypted = await provider_svc.get_decrypted_token(row.id)
    assert decrypted == plaintext


@pytest.mark.asyncio
async def test_get_decrypted_token_returns_none_when_unset(
    provider_svc: ProviderService,
) -> None:
    row = await provider_svc.create_provider(
        ProviderCreate(name=f"nt-{uuid4().hex[:6]}", type=ModelProvider.LOCAL)
    )
    assert await provider_svc.get_decrypted_token(row.id) is None


@pytest.mark.asyncio
async def test_get_decrypted_token_returns_none_for_missing_provider(
    provider_svc: ProviderService,
) -> None:
    assert await provider_svc.get_decrypted_token(uuid4()) is None
