"""Auto-extracted engine mixin -- see decomp/extract.py. Method bodies below are
moved verbatim from AgentOrchestrator (family: vault)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from roboco.config import settings
from roboco.runtime.orchestrator import (
    logger,
)

if TYPE_CHECKING:
    from roboco.runtime.engines._types import AgentOrchestratorSelf as _Base
else:
    _Base = object


class VaultEngine(_Base):
    """Mixin holding the "vault" methods moved out of AgentOrchestrator."""

    def _ensure_vault_assets_on_startup(self) -> None:
        """Best-effort, gated: never blocks/fails startup on a vault error."""
        if not settings.obsidian_vault_enabled:
            return
        try:
            from pathlib import Path

            from roboco.vault import ensure_vault_assets

            ensure_vault_assets(Path(settings.vault_path))
        except Exception as e:
            logger.warning("Vault asset bootstrap failed at startup", error=str(e))

    async def _vault_intake_loop(self) -> None:
        """Vault intake: on an interval, turn tagged notes into held drafts.

        Dormant unless BOTH ``obsidian_vault_enabled`` AND
        ``vault_intake_enabled`` are on — a standard deployment scans nothing.
        Every draft is held for the CEO; this loop never starts anything.
        """
        if not (settings.obsidian_vault_enabled and settings.vault_intake_enabled):
            return
        interval = settings.vault_intake_interval_seconds
        self._record_loop_heartbeat("vault_intake", interval)
        while self._running:
            try:
                await asyncio.sleep(interval)
                await self._run_vault_intake_cycle()
                self._record_loop_heartbeat("vault_intake", interval)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("vault-intake cycle failed")

    async def _run_vault_intake_cycle(self) -> None:
        """One vault-intake pass: run the engine, commit. Testable w/o the sleep."""
        from roboco.db import get_db_context
        from roboco.services.vault_intake_engine import get_vault_intake_engine

        async with get_db_context(pool="background") as db:
            await get_vault_intake_engine(db).run_cycle()
            await db.commit()

    async def _vault_janitor_loop(self) -> None:
        """Vault drift janitor: hourly tick, daily sweep + weekly report, both
        gated by a restart-proof state file rather than the loop's own
        cadence (see ``roboco.services.vault_janitor``).

        Dormant unless ``obsidian_vault_enabled`` — the umbrella flag.
        """
        if not settings.obsidian_vault_enabled:
            return
        from roboco.services.vault_janitor import JANITOR_LOOP_INTERVAL_SECONDS

        interval = JANITOR_LOOP_INTERVAL_SECONDS
        self._record_loop_heartbeat("vault_janitor", interval)
        while self._running:
            try:
                await asyncio.sleep(interval)
                await self._run_vault_janitor_cycle()
                self._record_loop_heartbeat("vault_janitor", interval)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("vault-janitor cycle failed")

    async def _run_vault_janitor_cycle(self) -> None:
        """One vault-janitor pass: run the service, commit. Testable w/o the sleep."""
        from roboco.db import get_db_context
        from roboco.services.vault_janitor import get_vault_janitor

        async with get_db_context(pool="background") as db:
            await get_vault_janitor(db).run_cycle()
            await db.commit()

    async def _vault_kb_loop(self) -> None:
        """Vault KB ingest: on an interval, embed changed notes under the
        allowlisted vault_kb_dirs into IndexType.VAULT_NOTES.

        Dormant unless BOTH ``obsidian_vault_enabled`` AND ``vault_kb_enabled``
        are on — a standard deployment embeds nothing.
        """
        if not (settings.obsidian_vault_enabled and settings.vault_kb_enabled):
            return
        interval = settings.vault_kb_interval_seconds
        self._record_loop_heartbeat("vault_kb", interval)
        while self._running:
            try:
                await asyncio.sleep(interval)
                await self._run_vault_kb_cycle()
                self._record_loop_heartbeat("vault_kb", interval)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("vault-kb cycle failed")

    async def _run_vault_kb_cycle(self) -> None:
        """One vault-KB pass: run the engine, commit. Testable w/o the sleep."""
        from roboco.db import get_db_context
        from roboco.services.vault_kb_engine import get_vault_kb_engine

        async with get_db_context(pool="background") as db:
            await get_vault_kb_engine(db).run_cycle()
            await db.commit()
