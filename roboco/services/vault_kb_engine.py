"""VaultKBEngine — human-authored vault note folders become one more RAG corpus.

V2 item 4 (KB ingest, the headline): ``vault_kb_dirs`` (default
``RoboCo/Notes``) are scanned recursively every cycle; changed notes
re-ingest into ``IndexType.VAULT_NOTES`` (``replace_chunks`` makes edits
idempotent), deleted notes deindex. Never covers Tasks/Journals/A2A/Agents/
Archive/Reports/_meta/.obsidian or the intake Inbox — config-load validation
(``Settings._validate_vault_kb_dirs``) rejects an overlapping, absolute, or
``..``-carrying ``vault_kb_dirs`` entry outright.

Containment is enforced again here as defense-in-depth: an allowlisted dir
whose resolved path escapes the vault root is skipped (warn-logged) before it
can contribute a single note, and every note must be a real file (no
symlinks) whose resolved path stays under the resolved vault root — a
symlinked ``.md`` pointing anywhere else on disk is never read.

Every note's BODY (frontmatter stripped) is screened through the shared
injection guard (``foundation.policy.injection_guard.screen_external_text``)
before it can reach the embedder. Unlike the intake watcher's
screen-and-neutralize posture, this is a hard GATE: an unflagged note is
indexed with its raw body (the untrusted-content envelope would pollute
retrieval chunks with non-note text), a flagged note is quarantined —
skipped, warn-logged, and marked with a feedback callout so the CEO sees why
it never made the index. The callout is stripped before hashing (shared
``foundation.policy.vault_notes`` convention with the intake watcher) so
appending it never itself re-triggers reprocessing, and the callout's own
presence is what stops a still-quarantined note from being re-flagged/
re-appended every cycle.

Dormant unless BOTH ``obsidian_vault_enabled`` AND ``vault_kb_enabled`` are on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from roboco.config import settings
from roboco.foundation.policy.injection_guard import screen_external_text
from roboco.foundation.policy.vault_notes import content_hash as _content_hash
from roboco.foundation.policy.vault_notes import split_frontmatter as _split_frontmatter
from roboco.models.optimal import IndexType
from roboco.services.base import BaseService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# Per-note size cap: an oversized note is skipped+logged rather than sent to
# the embedder (an accidentally-pasted huge log dump shouldn't blow up a scan
# cycle's embedding cost).
_MAX_NOTE_BYTES = 64 * 1024
# Per-cycle embed budget: a mass import (say a whole notes folder dropped in
# at once) ingests in 50-note slices, one slice per cycle, instead of one
# unbounded embedding burst. The deletion pass stays uncapped.
_MAX_INGEST_PER_CYCLE = 50
# The quarantine callout this engine appends (see _append_quarantine_callout).
# Presence-checked (not just stripped) so a still-quarantined note is neither
# re-logged nor double-stamped on every later cycle.
_QUARANTINE_CALLOUT_RE = re.compile(r"> \[!warning\] RoboCo: quarantined .*")


@dataclass(frozen=True)
class VaultKBCycleReport:
    """Per-cycle counters for the one summary log line."""

    ingested: int = 0
    skipped: int = 0
    quarantined: int = 0
    deleted: int = 0


class VaultKBEngine(BaseService):
    """One KB-ingest pass over the allowlisted vault note folders."""

    service_name = "vault_kb_engine"

    async def run_cycle(self) -> VaultKBCycleReport:
        """Scan, dedup by content hash, screen, ingest/quarantine, deindex
        removed notes. Empty report unless both the vault AND KB flags are on."""
        if not (settings.obsidian_vault_enabled and settings.vault_kb_enabled):
            return VaultKBCycleReport()

        from roboco.services.optimal import get_optimal_service

        optimal = await get_optimal_service()
        vault_root = Path(settings.vault_path)
        self._root_resolved = vault_root.resolve()
        self._cycle_ingested = 0
        self._cap_logged = False
        note_paths = self._scan(self._ensure_allowlisted_dirs(vault_root))
        tracked = await self._tracked_notes(optimal)

        ingested = skipped = quarantined = 0
        seen_paths: set[str] = set()
        for note_path in note_paths:
            outcome, rel_path = await self._process_note(
                optimal, note_path, vault_root, tracked
            )
            if rel_path is not None:
                seen_paths.add(rel_path)
            if outcome == "ingested":
                ingested += 1
            elif outcome == "quarantined":
                quarantined += 1
            else:
                skipped += 1

        deleted = await self._deindex_removed(optimal, tracked, seen_paths)
        report = VaultKBCycleReport(ingested, skipped, quarantined, deleted)
        self.log.info(
            "vault-kb cycle complete",
            ingested=ingested,
            skipped=skipped,
            quarantined=quarantined,
            deleted=deleted,
        )
        return report

    def _ensure_allowlisted_dirs(self, vault_root: Path) -> list[Path]:
        """Vault-relative ``vault_kb_dirs`` entries, created if missing so the
        CEO has a place to write notes into. Defense-in-depth behind the
        config validator: an entry whose resolved path escapes the vault root
        is skipped (never scanned, never mkdir'd) with one warning."""
        dirs: list[Path] = []
        for rel in (d.strip() for d in settings.vault_kb_dirs.split(",")):
            if not rel:
                continue
            candidate = vault_root / rel
            resolved = candidate.resolve()
            if resolved == self._root_resolved or not resolved.is_relative_to(
                self._root_resolved
            ):
                self.log.warning(
                    "vault-kb: dir escapes or equals the vault root (skipped)",
                    dir=rel,
                )
                continue
            candidate.mkdir(parents=True, exist_ok=True)
            dirs.append(candidate)
        return dirs

    def _scan(self, dirs: list[Path]) -> list[Path]:
        notes: list[Path] = []
        for d in dirs:
            notes.extend(sorted(d.rglob("*.md")))
        return notes

    async def _tracked_notes(self, optimal: Any) -> dict[str, dict[str, Any]]:
        """Every currently-indexed VAULT_NOTES doc, keyed by vault-relative path."""
        docs, _total = await optimal.list_indexed_documents(
            index_type=IndexType.VAULT_NOTES, offset=0, limit=10000
        )
        return {
            doc["extra_data"]["path"]: doc
            for doc in docs
            if doc.get("extra_data", {}).get("path")
        }

    async def _process_note(
        self,
        optimal: Any,
        note_path: Path,
        vault_root: Path,
        tracked: dict[str, dict[str, Any]],
    ) -> tuple[str, str | None]:
        """Returns ("ingested" / "quarantined" / "skipped", vault-relative
        path or None). Isolated per-note — an unexpected failure (embedder
        hiccup, bad read, underivable path) is logged and skipped rather than
        aborting the rest of the cycle. The rel-path derivation lives INSIDE
        the isolation so a pathological entry can't kill every later note."""
        rel_path: str | None = None
        try:
            rel_path = str(note_path.relative_to(vault_root))
            outcome = await self._process_note_unsafe(
                optimal, note_path, rel_path, tracked
            )
        except Exception as exc:
            self.log.warning(
                "vault-kb: note processing failed (skipped)",
                path=rel_path or str(note_path),
                error=str(exc),
            )
            return "skipped", rel_path
        return outcome, rel_path

    async def _process_note_unsafe(
        self,
        optimal: Any,
        note_path: Path,
        rel_path: str,
        tracked: dict[str, dict[str, Any]],
    ) -> str:
        if self._skip_unsafe_file(note_path, rel_path):
            return "skipped"

        raw = note_path.read_text(encoding="utf-8")
        content_hash = _content_hash(raw)
        existing = tracked.get(rel_path)
        if existing and existing["extra_data"].get("content_hash") == content_hash:
            return "skipped"
        if self._ingest_capped():
            return "skipped"

        # Screen + index the BODY only: frontmatter is Obsidian metadata, not
        # retrievable prose, and would pollute the chunks.
        _, body = _split_frontmatter(raw)
        screened = screen_external_text(body, source=f"vault_kb:{rel_path}")
        if screened.flagged:
            if existing is not None:
                # Was clean and indexed, now edited into a flagged state — its
                # stale prior content must not stay retrievable.
                await optimal.unindex_vault_note(rel_path)
            self._stamp_quarantine(note_path, rel_path, raw, screened.hits)
            return "quarantined"
        return await self._ingest(optimal, note_path, rel_path, body, content_hash)

    def _skip_unsafe_file(self, note_path: Path, rel_path: str) -> bool:
        """True for a note that must never be read: a symlink (would follow
        anywhere on disk), a resolved path escaping the vault root, or an
        oversized file."""
        if note_path.is_symlink() or not note_path.resolve().is_relative_to(
            self._root_resolved
        ):
            self.log.warning(
                "vault-kb: note is a symlink or escapes the vault root (skipped)",
                path=rel_path,
            )
            return True
        size = note_path.stat().st_size
        if size > _MAX_NOTE_BYTES:
            self.log.warning(
                "vault-kb: note oversized (skipped)", path=rel_path, size=size
            )
            return True
        return False

    def _ingest_capped(self) -> bool:
        """True once the per-cycle embed budget is spent; the tail of changed
        notes is left untouched for the next cycle (logged once)."""
        if self._cycle_ingested < _MAX_INGEST_PER_CYCLE:
            return False
        if not self._cap_logged:
            self.log.warning(
                "vault-kb: per-cycle ingest cap reached; deferring the rest",
                cap=_MAX_INGEST_PER_CYCLE,
            )
            self._cap_logged = True
        return True

    def _stamp_quarantine(
        self, note_path: Path, rel_path: str, raw: str, hits: list[str]
    ) -> None:
        """Warn-log + append the feedback callout exactly once — the callout's
        presence in ``raw`` is what keeps later cycles quiet."""
        if _QUARANTINE_CALLOUT_RE.search(raw) is not None:
            return
        self.log.warning(
            "vault-kb: injection pattern detected in note body (quarantined)",
            path=rel_path,
            hits=hits,
        )
        self._append_quarantine_callout(note_path)

    async def _ingest(
        self,
        optimal: Any,
        note_path: Path,
        rel_path: str,
        body: str,
        content_hash: str,
    ) -> str:
        title = note_path.stem.replace("-", " ").replace("_", " ").title()
        result = await optimal.index_vault_note(
            path=rel_path, title=title, content=body, content_hash=content_hash
        )
        if not result.success:
            self.log.warning(
                "vault-kb: ingest failed", path=rel_path, error=result.error
            )
            return "skipped"
        self._cycle_ingested += 1
        return "ingested"

    async def _deindex_removed(
        self,
        optimal: Any,
        tracked: dict[str, dict[str, Any]],
        seen_paths: set[str],
    ) -> int:
        removed = [path for path in tracked if path not in seen_paths]
        for path in removed:
            await optimal.unindex_vault_note(path)
        return len(removed)

    def _append_quarantine_callout(self, note_path: Path) -> None:
        """Best-effort: a filesystem error here never fails the cycle."""
        try:
            date_str = datetime.now(UTC).strftime("%Y-%m-%d")
            line = (
                "\n> [!warning] RoboCo: quarantined "
                f"(injection pattern detected) on {date_str}\n"
            )
            with note_path.open("a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError as exc:
            self.log.warning(
                "vault-kb: quarantine callout append failed",
                path=str(note_path),
                error=str(exc),
            )


def get_vault_kb_engine(session: AsyncSession) -> VaultKBEngine:
    """Build a VaultKBEngine for ``session``."""
    return VaultKBEngine(session)
