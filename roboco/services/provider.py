"""
Provider Service

CRUD for `provider_configs` rows (logical model-provider connections).
Mirrors the Fernet encryption patterns in `ProjectService` — empty-string
clears the token, `None` leaves unchanged, non-empty re-encrypts.

API route modules translate their pydantic request models into the
dataclasses defined here at the boundary, so services never depend on
api.schemas.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import select

from roboco.db.tables import ModelAssignmentTable, ProviderConfigTable
from roboco.services.base import BaseService, ConflictError, NotFoundError
from roboco.utils.crypto import EncryptionError, decrypt_token, encrypt_token

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.models.base import ModelProvider


@dataclass(frozen=True)
class ProviderCreate:
    """Service-side shape for creating a provider config."""

    name: str
    type: ModelProvider
    base_url: str | None = None
    auth_token: str | None = None  # plaintext; encrypted before persist
    enabled: bool = True


@dataclass(frozen=True)
class ProviderUpdate:
    """Service-side shape for updating a provider config.

    `auth_token` is tri-state: `None` leaves unchanged, `""` clears, any
    other value re-encrypts. Matches `ProjectService.update` semantics.
    """

    name: str | None = None
    base_url: str | None = None
    # `_SENTINEL` is the marker for "no change"; routes translate their
    # pydantic model — with Python `None` as both "unset" and "clear to
    # NULL" depending on field — into explicit values.
    auth_token: str | None = None
    clear_auth_token: bool = False  # if True, force token → NULL
    enabled: bool | None = None


class ProviderService(BaseService):
    """Manages `provider_configs` rows + their Fernet-encrypted tokens."""

    service_name: ClassVar[str] = "provider"

    # =========================================================================
    # QUERIES
    # =========================================================================

    async def list_providers(
        self, *, include_disabled: bool = False
    ) -> list[ProviderConfigTable]:
        query = select(ProviderConfigTable)
        if not include_disabled:
            query = query.where(ProviderConfigTable.enabled.is_(True))
        query = query.order_by(ProviderConfigTable.name)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_provider(self, provider_id: UUID) -> ProviderConfigTable | None:
        result = await self.session.execute(
            select(ProviderConfigTable).where(ProviderConfigTable.id == provider_id)
        )
        return result.scalar_one_or_none()

    async def get_provider_or_raise(self, provider_id: UUID) -> ProviderConfigTable:
        provider = await self.get_provider(provider_id)
        if not provider:
            raise NotFoundError(resource_type="Provider", resource_id=str(provider_id))
        return provider

    async def get_by_name(self, name: str) -> ProviderConfigTable | None:
        result = await self.session.execute(
            select(ProviderConfigTable).where(ProviderConfigTable.name == name)
        )
        return result.scalar_one_or_none()

    # =========================================================================
    # MUTATIONS
    # =========================================================================

    async def create_provider(self, data: ProviderCreate) -> ProviderConfigTable:
        """Create a provider. Raises ConflictError on duplicate name."""
        existing = await self.get_by_name(data.name)
        if existing:
            raise ConflictError(
                f"Provider with name '{data.name}' already exists",
                resource_type="provider",
            )

        encrypted: str | None = None
        if data.auth_token:
            try:
                encrypted = encrypt_token(data.auth_token)
            except EncryptionError as e:
                self.log.error("Failed to encrypt auth token", error=str(e))
                raise

        row = ProviderConfigTable(
            name=data.name,
            type=data.type,
            base_url=data.base_url,
            auth_token_encrypted=encrypted,
            enabled=data.enabled,
        )
        self.session.add(row)
        await self.session.flush()

        self.log.info(
            "Provider created",
            provider_id=str(row.id),
            name=data.name,
            type=data.type.value,
            has_auth_token=bool(encrypted),
        )
        return row

    async def _apply_name_change(self, row: ProviderConfigTable, new_name: str) -> None:
        """Set row.name with duplicate-name guard."""
        if new_name == row.name:
            return
        dup = await self.get_by_name(new_name)
        if dup and dup.id != row.id:
            raise ConflictError(
                f"Provider with name '{new_name}' already exists",
                resource_type="provider",
            )
        row.name = new_name

    def _apply_auth_token_change(
        self, row: ProviderConfigTable, data: ProviderUpdate
    ) -> None:
        """Tri-state token update: clear, set, or leave unchanged."""
        if data.clear_auth_token:
            row.auth_token_encrypted = None
            self.log.info("Provider auth token cleared", provider_id=str(row.id))
            return
        if not data.auth_token:
            return
        try:
            row.auth_token_encrypted = encrypt_token(data.auth_token)
        except EncryptionError as e:
            self.log.error("Failed to encrypt auth token", error=str(e))
            raise
        self.log.info("Provider auth token updated", provider_id=str(row.id))

    async def update_provider(
        self, provider_id: UUID, data: ProviderUpdate
    ) -> ProviderConfigTable | None:
        """Apply a patch to a provider. Tri-state semantics on `auth_token`."""
        row = await self.get_provider(provider_id)
        if not row:
            return None

        if data.name is not None:
            await self._apply_name_change(row, data.name)
        if data.base_url is not None:
            # Empty string → clear to NULL (matches git-token convention).
            row.base_url = data.base_url or None
        if data.enabled is not None:
            row.enabled = data.enabled

        self._apply_auth_token_change(row, data)

        await self.session.flush()
        return row

    async def delete_provider(self, provider_id: UUID) -> None:
        """Delete a provider. 409 if any assignment references it."""
        row = await self.get_provider_or_raise(provider_id)

        ref_count_q = select(ModelAssignmentTable).where(
            ModelAssignmentTable.provider_config_id == provider_id
        )
        ref_result = await self.session.execute(ref_count_q)
        if ref_result.first():
            raise ConflictError(
                "Provider is referenced by one or more model assignments; "
                "remove those first.",
                resource_type="provider",
            )

        await self.session.delete(row)
        await self.session.flush()
        self.log.info("Provider deleted", provider_id=str(provider_id))

    # =========================================================================
    # DECRYPTION
    # =========================================================================

    async def get_decrypted_token(self, provider_id: UUID) -> str | None:
        """Return the decrypted token for a provider, or None if unset."""
        row = await self.get_provider(provider_id)
        if not row or not row.auth_token_encrypted:
            return None
        try:
            return decrypt_token(row.auth_token_encrypted)
        except EncryptionError as e:
            self.log.error(
                "Failed to decrypt provider auth token",
                provider_id=str(provider_id),
                error=str(e),
            )
            raise


def get_provider_service(session: AsyncSession) -> ProviderService:
    """Get a ProviderService instance."""
    return ProviderService(session)
