"""VaultIntakeEngine — vault notes tagged ``#roboco`` become HELD intake drafts.

The vexa-inspired input loop (V1 item 4). Mirrors the RoadmapEngine/XEngine
"detect -> originate a CEO-gated artifact -> hold" shape:

* **Default OFF.** Both ``obsidian_vault_enabled`` AND ``vault_intake_enabled``
  must be on — the engine is inert if either is off.
* **Never starts anything.** Every draft is HELD (``confirmed_by_human=False``,
  owned by the Secretary, ``source=vault_note``) — skipped by every
  dispatcher exactly like an X post (``_is_held_ceo_source``). The CEO
  reviews it like any other backlog task before it can be worked.
* **Local model only.** Extraction runs on the local LLM (MemoryDistiller
  posture) with a deterministic fallback (first heading / raw body /
  checkbox lines) on any failure — never a cloud LLM in the hot path.
* **Dedup ledger.** ``vault_seen_notes`` keys on (vault-relative path,
  content hash) so an unchanged note is never reprocessed, but an edited one
  is eligible again. The hash excludes RoboCo's own feedback callout so
  appending it after processing does not itself trigger a reprocess.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import httpx
import yaml
from sqlalchemy import select

from roboco.config import settings
from roboco.db.tables import VaultSeenNoteTable
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType, Team
from roboco.services.base import BaseService
from roboco.services.project import get_project_service
from roboco.services.task import VAULT_NOTE_SOURCE, TaskCreateRequest, get_task_service

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import ProjectTable, TaskTable

_CHAT_TIMEOUT_SECONDS = 60.0
_AC_MAX_ITEMS = 7
_AC_MAX_ITEM_CHARS = 200
_TITLE_MAX_CHARS = 200
_DEFAULT_AC = "CEO reviews and starts this drafted task"

# Frontmatter block at the start of the file (mirrors vault_writer's own
# helper — a local copy, since inbox notes are arbitrary CEO-authored
# markdown, a different trust/shape boundary than the projection core's own
# generated notes).
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
# A whole `#roboco` tag, not a prefix of a longer tag (`#roboco/idea`) or word.
_INLINE_TAG_RE = re.compile(r"(?<![\w/-])#roboco(?![\w/-])")
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
_CHECKBOX_RE = re.compile(r"^\s*-\s*\[ \]\s*(.+?)\s*$", re.MULTILINE)
# The feedback callout this engine appends (see _append_feedback_callout).
# Stripped before hashing so appending it doesn't change the ledger key.
_FEEDBACK_CALLOUT_RE = re.compile(r"\n?> \[!info\] RoboCo: drafted .*(?:\n|$)")


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Frontmatter dict + body, or ({}, text) with no frontmatter block."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    loaded = yaml.safe_load(m.group(1))
    return (loaded if isinstance(loaded, dict) else {}), text[m.end() :]


def _has_roboco_tag(frontmatter: dict[str, Any], body: str) -> bool:
    """True if frontmatter ``tags`` (list or scalar) or the body carries a
    whole ``#roboco`` tag."""
    tags = frontmatter.get("tags")
    if isinstance(tags, str):
        tags = [tags]
    if isinstance(tags, list) and any(
        str(t).strip().lstrip("#") == "roboco" for t in tags
    ):
        return True
    return bool(_INLINE_TAG_RE.search(body))


def _content_hash(raw_text: str) -> str:
    """Sha256 of the note with RoboCo's own feedback callout stripped out."""
    stable = _FEEDBACK_CALLOUT_RE.sub("", raw_text)
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def _clamp_action_items(items: list[str]) -> list[str]:
    cleaned = [i.strip()[:_AC_MAX_ITEM_CHARS] for i in items if i.strip()]
    return cleaned[:_AC_MAX_ITEMS]


@dataclass(frozen=True)
class _NoteExtraction:
    """title + description + action items pulled from one vault note —
    bundled so ``_originate`` doesn't need one param per field."""

    title: str
    description: str
    action_items: list[str]


def _deterministic_extract(body: str, fallback_title: str) -> _NoteExtraction:
    """Local-model-failure fallback: first heading, raw body, checkbox lines."""
    heading = _HEADING_RE.search(body)
    title = heading.group(1).strip() if heading else fallback_title
    action_items = [m.group(1).strip() for m in _CHECKBOX_RE.finditer(body)]
    description = body.strip() or f"Vault note: {title}"
    return _NoteExtraction(title, description, action_items)


def _extraction_prompt(body: str) -> str:
    return (
        "Extract a task draft from this Obsidian vault note. Reply with ONLY "
        'a JSON object: {"title": <short title>, "description": <1-3 '
        'sentence summary>, "action_items": [<action item>, ...]}. No '
        "markdown fences, no commentary.\n\n"
        f"Note:\n{body.strip()}\n"
    )


async def _chat(prompt: str) -> str | None:
    """One local-LLM chat call (OpenAI-compatible); None on a non-success."""
    async with httpx.AsyncClient(timeout=_CHAT_TIMEOUT_SECONDS) as client:
        resp = await client.post(
            f"{settings.local_llm_base_url}/chat/completions",
            json={
                "model": settings.local_llm_model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 400,
            },
        )
        if not resp.is_success:
            return None
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            return None
        content = choices[0].get("message", {}).get("content")
        return content if isinstance(content, str) else None


def _parse_extraction(raw: str) -> _NoteExtraction | None:
    """Parse the local model's JSON reply; None on any malformed shape."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    title = str(data.get("title") or "").strip()
    description = str(data.get("description") or "").strip()
    if not title or not description:
        return None
    raw_items = data.get("action_items")
    items = [str(i) for i in raw_items] if isinstance(raw_items, list) else []
    return _NoteExtraction(title, description, items)


class VaultIntakeEngine(BaseService):
    """Turn ``#roboco``-tagged vault notes into HELD intake drafts."""

    service_name = "vault_intake_engine"

    async def run_cycle(self) -> list[TaskTable]:
        """One intake pass: scan, dedup, extract, hold. Empty list unless
        both the vault AND intake flags are on, the intake dir exists, the
        open-draft cap isn't already reached, and the RoboCo project
        resolves."""
        if not (settings.obsidian_vault_enabled and settings.vault_intake_enabled):
            return []
        intake_dir = Path(settings.vault_path) / settings.vault_intake_dir
        if not intake_dir.is_dir():
            return []
        task_svc = get_task_service(self.session)
        open_count = len(await task_svc.list_open_vault_note_drafts())
        if open_count >= settings.vault_intake_max_open_drafts:
            self.log.info("vault-intake: open-draft cap reached; skipping cycle")
            return []
        project = await self._roboco_project()
        if project is None or project.id is None:
            self.log.warning("vault-intake: RoboCo project not resolvable; skipping")
            return []
        return await self._process_notes(
            sorted(intake_dir.glob("*.md")), cast("UUID", project.id), open_count
        )

    async def _roboco_project(self) -> ProjectTable | None:
        slug = (settings.self_heal_project_slug or "roboco-api").strip()
        return await get_project_service(self.session).get_by_slug(slug)

    async def _process_notes(
        self, note_paths: list[Path], project_id: UUID, open_count: int
    ) -> list[TaskTable]:
        """Per-note isolation: one bad note (unreadable, malformed) is
        skipped and logged rather than aborting the rest of the cycle."""
        originated: list[TaskTable] = []
        for note_path in note_paths:
            if len(originated) >= settings.vault_intake_max_per_cycle:
                break
            if open_count + len(originated) >= settings.vault_intake_max_open_drafts:
                break
            try:
                task = await self._process_note(note_path, project_id)
            except OSError as exc:
                self.log.warning(
                    "vault-intake: note read failed (skipped)",
                    path=str(note_path),
                    error=str(exc),
                )
                continue
            if task is not None:
                originated.append(task)
        return originated

    async def _process_note(
        self, note_path: Path, project_id: UUID
    ) -> TaskTable | None:
        raw = note_path.read_text(encoding="utf-8")
        frontmatter, body = _split_frontmatter(raw)
        if not _has_roboco_tag(frontmatter, body):
            return None
        rel_path = str(note_path.relative_to(Path(settings.vault_path)))
        content_hash = _content_hash(raw)
        if await self._already_seen(rel_path, content_hash):
            return None
        extraction = await self._extract(body, note_path.stem)
        extraction = _NoteExtraction(
            extraction.title,
            extraction.description,
            _clamp_action_items(extraction.action_items),
        )
        task = await self._originate(
            project_id=project_id,
            rel_path=rel_path,
            content_hash=content_hash,
            extraction=extraction,
        )
        self.session.add(
            VaultSeenNoteTable(note_path=rel_path, content_hash=content_hash)
        )
        await self.session.flush()
        self._append_feedback_callout(note_path, task)
        return task

    async def _extract(self, body: str, fallback_title: str) -> _NoteExtraction:
        try:
            raw = await _chat(_extraction_prompt(body))
        except Exception as exc:
            self.log.warning(
                "vault-intake: local-model extraction failed (fallback)",
                error=str(exc),
            )
            raw = None
        parsed = _parse_extraction(raw) if raw else None
        return parsed or _deterministic_extract(body, fallback_title)

    async def _already_seen(self, rel_path: str, content_hash: str) -> bool:
        result = await self.session.execute(
            select(VaultSeenNoteTable.id)
            .where(
                VaultSeenNoteTable.note_path == rel_path,
                VaultSeenNoteTable.content_hash == content_hash,
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def _originate(
        self,
        *,
        project_id: UUID,
        rel_path: str,
        content_hash: str,
        extraction: _NoteExtraction,
    ) -> TaskTable:
        """Open ONE PENDING, HELD draft owned by the Secretary."""
        task_svc = get_task_service(self.session)
        task = await task_svc.create(
            TaskCreateRequest(
                title=f"Vault note: {extraction.title}"[:_TITLE_MAX_CHARS],
                description=extraction.description,
                acceptance_criteria=extraction.action_items or [_DEFAULT_AC],
                team=Team.MAIN_PM,
                assigned_to=_foundation.AGENTS["secretary-1"].uuid,
                created_by=_foundation.AGENTS["system"].uuid,
                task_type=TaskType.ADMINISTRATIVE,
                nature=TaskNature.NON_TECHNICAL,
                estimated_complexity=Complexity.LOW,
                project_id=project_id,
                status=TaskStatus.PENDING,
                source=VAULT_NOTE_SOURCE,
                confirmed_by_human=False,  # HELD; never dispatched
            )
        )
        markers.set_vault_note_ref(
            task,
            {
                "path": rel_path,
                "content_hash": content_hash,
                "action_items": extraction.action_items,
            },
        )
        await self.session.flush()
        self.log.info(
            "vault-intake: held draft drafted", task_id=str(task.id), path=rel_path
        )
        return task

    def _append_feedback_callout(self, note_path: Path, task: TaskTable) -> None:
        """Best-effort: a filesystem error here never fails the cycle."""
        try:
            id8 = str(task.id)[:8]
            date_str = datetime.now(UTC).strftime("%Y-%m-%d")
            line = f"\n> [!info] RoboCo: drafted {task.title} ({id8}) on {date_str}\n"
            with note_path.open("a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError as exc:
            self.log.warning(
                "vault-intake: feedback callout append failed",
                path=str(note_path),
                error=str(exc),
            )


def get_vault_intake_engine(session: AsyncSession) -> VaultIntakeEngine:
    """Build a VaultIntakeEngine for ``session``."""
    return VaultIntakeEngine(session)
