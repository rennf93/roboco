"""LibrarianEngine — Librarian (Board Program), org-scoped.

Mirrors ``roboco.services.sentinel_engine.SentinelEngine``'s "detect ->
originate a CEO-gated artifact -> hold, complete-at-propose" shape AND its
server-assembled-evidence posture (``mining_context``): the Auditor cannot
run aggregate SQL itself, so this engine gathers recurring-learning-topic +
existing-playbook-title evidence ahead of the spawn. Org-scoped (spec §4 — it
mines journals/learnings org-wide, not a repo) but the exploration task
itself still needs a resolvable ``project_id`` (``TaskService.
_require_target_or_umbrella``, the same RoboCo-project-anchor resolution
roadmap/periscope/sentinel all use).

The Auditor does NOT gain ``draft_playbook`` on its manifest for this
program — see ``content_actions.propose_playbook_drafts``' docstring and
``role_config.py``'s ``_AUDITOR_DO`` comment for the precedent (Coroner
already established this: "auditor curates but does not draft" stays a
do-verb-surface invariant, locked by ``test_playbook_verbs.py``). A
Librarian-authored playbook is created by calling ``PlaybookService.draft()``
directly, exactly like Coroner's ``_draft_coroner_playbook``.

* **No master enable flag.** Armed via ``roboco.services.board_programs.
  program_armed`` — the settings-store ``board_program.librarian.enabled``
  key is the ONLY arming path (no legacy flag exists for it); off by default
  like every other program.
* **One open cycle at a time.** Dedup by ``source=board_librarian``
  non-terminal tasks.
* **The engine never authors content.** It opens ONE held, PENDING mining
  task assigned to the Auditor (``Team.BOARD``, ``confirmed_by_human=False``);
  the board dispatcher spawns the Auditor, who mines journals/learnings and
  calls ``propose_playbook_drafts`` exactly once, which drafts 1-3 real
  playbooks and completes the mining task in the same call (a mining cycle
  has no per-item CEO decision to wait on — mirrors ``PeriscopeEngine``'s
  complete-at-propose asymmetry, not roadmap/pest-control's per-item queue).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from sqlalchemy import func, select

from roboco.config import settings
from roboco.foundation import identity as _foundation
from roboco.models.base import (
    Complexity,
    JournalEntryType,
    PlaybookStatus,
    TaskNature,
    TaskStatus,
    TaskType,
    Team,
)
from roboco.services.base import BaseService
from roboco.services.board_programs import program_armed
from roboco.services.project import get_project_service
from roboco.services.task import LIBRARIAN_SOURCE, TaskCreateRequest, get_task_service

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import ProjectTable, TaskTable
    from roboco.services.task import TaskService

_EXPLORATION_TITLE = "Librarian playbook-mining cycle"
_EXPLORATION_DESCRIPTION = (
    "Mine journals/learnings for repeated patterns nobody has turned into a "
    "playbook yet, and draft 1-3 of them via propose_playbook_drafts(). "
    "Playbook curation today is reactive — the Auditor only judges what "
    "delivery roles happen to draft; this is the proactive half. Each draft "
    "lands in the normal pending-playbook curation queue, reviewed like any "
    "other draft (never self-approved here)."
)

# Mining-context caps — the prompt injection stays bounded regardless of how
# large the journal/playbook corpus has grown. Mirrors SentinelEngine's /
# PestControlEngine's evidence_context caps.
_LEARNING_TOPIC_LIMIT = 10
_EXISTING_PLAYBOOK_LIMIT = 20
_MIN_RECURRING_LEARNING_COUNT = 2


class LibrarianEngine(BaseService):
    """Originate ONE held Librarian-mining cycle for the Auditor."""

    service_name = "librarian_engine"

    async def run_cycle(self) -> TaskTable | None:
        """Originate one held mining task, or None (no-op).

        No-ops when the program isn't armed, a cycle is already open, or the
        RoboCo project (the task's required FK anchor) isn't resolvable.
        Never authors content itself — the Auditor does, via
        ``propose_playbook_drafts`` once spawned by the board dispatcher.
        """
        if not await program_armed(self.session, "librarian"):
            return None
        task_svc = get_task_service(self.session)
        if await task_svc.list_open_librarian_cycles():
            return None  # one open cycle at a time
        project = await self._roboco_project()
        if project is None or project.id is None:
            self.log.warning(
                "librarian-engine: RoboCo project not resolvable; skipping"
            )
            return None
        return await self._originate(task_svc, cast("UUID", project.id))

    async def _roboco_project(self) -> ProjectTable | None:
        slug = (settings.self_heal_project_slug or "roboco-api").strip()
        return await get_project_service(self.session).get_by_slug(slug)

    async def _originate(self, task_svc: TaskService, project_id: UUID) -> TaskTable:
        """Open ONE PENDING, HELD mining task assigned to the Auditor."""
        task = await task_svc.create(
            TaskCreateRequest(
                title=_EXPLORATION_TITLE,
                description=_EXPLORATION_DESCRIPTION,
                acceptance_criteria=[
                    "propose_playbook_drafts() is called once with 1-3 "
                    "pattern-evidenced playbook drafts"
                ],
                team=Team.BOARD,
                assigned_to=_foundation.AGENTS["auditor"].uuid,
                created_by=_foundation.AGENTS["system"].uuid,
                task_type=TaskType.ADMINISTRATIVE,
                nature=TaskNature.NON_TECHNICAL,
                estimated_complexity=Complexity.LOW,
                project_id=project_id,
                status=TaskStatus.PENDING,
                source=LIBRARIAN_SOURCE,
                confirmed_by_human=False,  # HELD; board-dispatched, not delivery
            )
        )
        await self.session.flush()
        self.log.info("librarian mining cycle opened (Auditor)", task_id=str(task.id))
        return task

    async def mining_context(self) -> str:
        """Server-assembled mining evidence for the Auditor's prompt —
        recurring learning-journal topics + existing playbook titles (so the
        Auditor doesn't propose a duplicate), capped so the prompt stays
        bounded. The Auditor cannot run these aggregate queries itself (no
        SQL tool), so the engine gathers them ahead of the spawn (mirrors
        ``SentinelEngine.evidence_context``'s shape).

        Deliberately does NOT mine org-memory's auto-extracted completion
        lessons (``learning.py``'s ``RecordLearningParams`` capture): those
        land ONLY in the raw pgvector ``chunks_learnings`` table
        (``VectorStore``, a plain asyncpg connection outside this service's
        ORM session) — no cheap, pure-ORM surface exists for them the way
        ``journal_entries`` gives agent-authored ``note(scope='learning')``
        entries (no matching ``IndexedDocumentTable`` row is ever written for
        a learning ingest — only file-based corpora get one). Reaching them
        would need either a real semantic-search embedding call (not
        "cheap") or a second raw connection this engine doesn't hold, so
        this mining pass skips them cleanly rather than faking cheapness.
        """
        sections = [
            ("Recurring learning topics", await self._recurring_learning_topics()),
            (
                "Existing playbook titles (avoid duplicating)",
                await self._existing_playbook_titles(),
            ),
        ]
        return "\n\n".join(
            f"{title}:\n" + "\n".join(lines) for title, lines in sections if lines
        )

    async def _recurring_learning_topics(self) -> list[str]:
        """Journal-entry titles (``type=LEARNING``, never a private
        reflection) that recur across 2+ entries, most-repeated first — the
        actual "pattern" signal Librarian mines. Falls back to a recent
        sample when nothing has recurred yet, so a fresh org still gets SOME
        signal on its first cycles."""
        from roboco.db.tables import JournalEntryTable

        recurring = await self.session.execute(
            select(JournalEntryTable.title, func.count())
            .where(
                JournalEntryTable.type == JournalEntryType.LEARNING,
                JournalEntryTable.is_private.is_(False),
            )
            .group_by(JournalEntryTable.title)
            .having(func.count() >= _MIN_RECURRING_LEARNING_COUNT)
            .order_by(func.count().desc())
            .limit(_LEARNING_TOPIC_LIMIT)
        )
        rows = [f"- {title!r} recurred {count}x" for title, count in recurring.all()]
        if rows:
            return rows
        sample = await self.session.execute(
            select(JournalEntryTable.title)
            .where(
                JournalEntryTable.type == JournalEntryType.LEARNING,
                JournalEntryTable.is_private.is_(False),
            )
            .order_by(JournalEntryTable.timestamp.desc())
            .limit(_LEARNING_TOPIC_LIMIT)
        )
        return [f"- {title!r}" for (title,) in sample.all()]

    async def _existing_playbook_titles(self) -> list[str]:
        from roboco.db.tables import PlaybookTable

        result = await self.session.execute(
            select(PlaybookTable.title)
            .where(PlaybookTable.status != PlaybookStatus.ARCHIVED.value)
            .order_by(PlaybookTable.created_at.desc())
            .limit(_EXISTING_PLAYBOOK_LIMIT)
        )
        return [f"- {title}" for (title,) in result.all()]

    async def existing_playbook_titles_lower(self) -> set[str]:
        """Live, UNBOUNDED (unlike ``mining_context``'s LIMIT-20 excerpt)
        case-insensitive title set — ``propose_playbook_drafts``' dedup check
        must catch a collision anywhere in the store, not just among the
        most-recent 20 the prompt shows."""
        from roboco.db.tables import PlaybookTable

        result = await self.session.execute(
            select(PlaybookTable.title).where(
                PlaybookTable.status != PlaybookStatus.ARCHIVED.value
            )
        )
        return {title.strip().lower() for (title,) in result.all()}


def get_librarian_engine(session: AsyncSession) -> LibrarianEngine:
    """Build a LibrarianEngine for ``session``."""
    return LibrarianEngine(session)
