"""VaultWriter — pure Obsidian-vault materializer (projection core, V1+V2).

Entity -> markdown note (frontmatter + body). Idempotent per entity id: the
same input always yields the same file, and safe to re-run (rebuild/CLI,
retried seams). No DB/network access — callers assemble plain dataclasses
from their own service layer and hand them to this module.

Layout (``docs/internal/specs/2026-07-09-obsidian-vault.md`` §Vault layout)::

    RoboCo/
      Tasks/<project-slug>/<title> (<id8>).md
      Archive/<year>/Tasks/<project-slug>/<title> (<id8>).md
      Journals/<agent-slug>/<date> <title> (<id8>).md
      A2A/<date> <agents> (<thread-id8>).md
      Agents/<slug>.md
      Reports/<ISO-week>.md
      _meta/

Link stability: every note carries ``aliases: [<id8>]`` in frontmatter, so a
cross-link is always written as ``[[<id8>|<title>]]`` — Obsidian resolves it
via the alias regardless of the target's CURRENT filename. A title edit
therefore updates the body's title line (and, for a full ``write_task``
re-render, the frontmatter) without ever renaming the file or breaking a
link elsewhere in the vault.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from datetime import datetime

_ILLEGAL_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')
_MAX_TITLE_LEN = 80
_NARRATIVE_PLACEHOLDER = "_Pending Auditor curation._"
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)


def _safe_title(title: str) -> str:
    """Filesystem-safe, length-capped title for a filename component."""
    cleaned = _ILLEGAL_FILENAME_CHARS.sub("", title or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return (cleaned or "untitled")[:_MAX_TITLE_LEN]


def _id8(entity_id: str) -> str:
    return str(entity_id)[:8]


def _find_by_id8(directory: Path, id8: str) -> Path | None:
    """Locate an existing note by its stable id8 suffix, if the dir exists."""
    if not directory.exists():
        return None
    matches = sorted(directory.glob(f"*({id8}).md"))
    return matches[0] if matches else None


def _rfind_by_id8(root: Path, id8: str) -> Path | None:
    """Recursive variant of ``_find_by_id8`` for callers that don't know
    which subfolder (e.g. project slug) a note lives under."""
    if not root.exists():
        return None
    matches = sorted(root.rglob(f"*({id8}).md"))
    return matches[0] if matches else None


def _drop_none(mapping: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in mapping.items() if v is not None}


def _render_note(frontmatter: dict[str, Any], body: str) -> str:
    fm = yaml.safe_dump(
        _drop_none(frontmatter), sort_keys=False, default_flow_style=False
    ).strip()
    return f"---\n{fm}\n---\n\n{body.rstrip()}\n"


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    loaded = yaml.safe_load(m.group(1))
    fm = loaded if isinstance(loaded, dict) else {}
    return fm, text[m.end() :]


@dataclass(frozen=True)
class TaskLinkRef:
    """A wikilink target: id (any length; truncated to id8) + display title."""

    id: str
    title: str = ""


def _wikilink(ref: TaskLinkRef) -> str:
    id8 = _id8(ref.id)
    return f"[[{id8}|{ref.title}]]" if ref.title else f"[[{id8}]]"


@dataclass(frozen=True)
class FindingRow:
    """One revision-findings-ledger row, trimmed to what a vault note needs.

    ``id8`` mirrors the ``[F-<id8>]`` rendering convention used in notes/A2A
    bodies elsewhere so a CEO reading the vault can cross-reference the same
    id the agents saw."""

    id8: str
    severity: str
    file: str | None
    line: int | None
    expected: str
    actual: str
    fix: str | None
    status: str
    round: int


_FINDINGS_CAP = 20


def _finding_line(f: FindingRow) -> str:
    loc = (f"{f.file}:{f.line}" if f.line is not None else f.file) if f.file else "—"
    tail = f"{f.expected} → {f.actual}"
    if f.fix:
        tail += f" → {f.fix}"
    return f"- [F-{f.id8}] ({f.severity}, round {f.round}, {f.status}) {loc} — {tail}"


@dataclass(frozen=True)
class TaskNoteData:
    """Deterministic content for one task note. ``narrative`` is None until
    the Auditor curates it (a placeholder is rendered instead). ``archive_year``
    is set (by the shared assembler) when the task is terminal and past
    ``vault_archive_days`` — routes ``write_task`` to ``Archive/<year>/``
    instead of ``Tasks/``; None keeps it live."""

    id: str
    title: str
    project_slug: str
    description: str
    status: str
    team: str
    priority: int
    task_type: str
    acceptance_criteria: tuple[str, ...] = ()
    pr_number: int | None = None
    pr_url: str | None = None
    parent: TaskLinkRef | None = None
    subtasks: tuple[TaskLinkRef, ...] = ()
    dependencies: tuple[TaskLinkRef, ...] = ()
    batch_id: str | None = None
    narrative: str | None = None
    archive_year: int | None = None
    # Revision-findings ledger, newest round first. Empty for a task never
    # bounced — the section renders only when non-empty (see ``_task_body``).
    findings: tuple[FindingRow, ...] = ()


def _task_frontmatter(data: TaskNoteData, id8: str) -> dict[str, Any]:
    return {
        "aliases": [id8],
        "status": data.status,
        "team": data.team,
        "priority": data.priority,
        "pr": data.pr_url or (f"#{data.pr_number}" if data.pr_number else None),
        "parent": f"[[{_id8(data.parent.id)}]]" if data.parent else None,
        "batch": _id8(data.batch_id) if data.batch_id else None,
        "tags": [f"status/{data.status}", f"team/{data.team}"],
    }


def _findings_section(findings: tuple[FindingRow, ...]) -> list[str]:
    """The ``## Findings`` block: capped newest-first + an overflow line.

    Empty (``[]``) for no findings — the caller appends unconditionally, so
    the section's own presence check doesn't add a branch to ``_task_body``.
    """
    if not findings:
        return []
    lines = ["", "## Findings"]
    lines += [_finding_line(f) for f in findings[:_FINDINGS_CAP]]
    overflow = len(findings) - _FINDINGS_CAP
    if overflow > 0:
        lines.append(f"- … {overflow} more (see the panel Findings tab)")
    return lines


def _task_body(data: TaskNoteData) -> list[str]:
    body: list[str] = [f"# {data.title}", "", (data.description or "").strip()]
    if data.acceptance_criteria:
        body += ["", "## Acceptance Criteria"]
        body += [f"- [ ] {c}" for c in data.acceptance_criteria]
    if data.parent:
        body += ["", "## Parent", f"- {_wikilink(data.parent)}"]
    if data.subtasks:
        body += ["", "## Subtasks"]
        body += [f"- {_wikilink(s)}" for s in data.subtasks]
    if data.dependencies:
        body += ["", "## Dependencies"]
        body += [f"- {_wikilink(d)}" for d in data.dependencies]
    body += _findings_section(data.findings)
    body += ["", "## Narrative", (data.narrative or _NARRATIVE_PLACEHOLDER).strip()]
    return body


@dataclass(frozen=True)
class JournalNoteData:
    entry_id: str
    agent_slug: str
    scope: str
    title: str
    content: str
    timestamp: datetime
    task_ref: TaskLinkRef | None = None


@dataclass(frozen=True)
class A2AMessageData:
    conversation_id: str
    message_id: str
    from_agent: str
    to_agent: str
    content: str
    timestamp: datetime
    task_ref: TaskLinkRef | None = None


@dataclass(frozen=True)
class AgentNoteData:
    slug: str
    name: str
    role: str
    team: str | None = None


class VaultWriter:
    """Pure file-system materializer, rooted at ``root`` (``ROBOCO_VAULT_PATH``)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    # --- paths ---------------------------------------------------------- #

    def _tasks_root(self) -> Path:
        return self.root / "RoboCo" / "Tasks"

    def _archive_root(self) -> Path:
        return self.root / "RoboCo" / "Archive"

    def _journals_root(self) -> Path:
        return self.root / "RoboCo" / "Journals"

    def _a2a_root(self) -> Path:
        return self.root / "RoboCo" / "A2A"

    def _agents_root(self) -> Path:
        return self.root / "RoboCo" / "Agents"

    # --- tasks ------------------------------------------------------------ #

    def _task_directory(self, data: TaskNoteData) -> Path:
        project = data.project_slug or "unassigned"
        if data.archive_year is not None:
            return self._archive_root() / str(data.archive_year) / "Tasks" / project
        return self._tasks_root() / project

    def find_task_note(self, task_id: str) -> Path | None:
        """Locate a task's note wherever it lives (``Tasks/`` or
        ``Archive/<year>/Tasks/``), or None if never materialized. Stable
        across an archival move — the search is id8-keyed, not path-keyed.
        Public: also used by the drift janitor's sample-verification pass."""
        id8 = _id8(task_id)
        return _rfind_by_id8(self._tasks_root(), id8) or _rfind_by_id8(
            self._archive_root(), id8
        )

    def task_note_status(self, note_path: Path) -> str | None:
        """Frontmatter ``status`` of an already-located note (janitor drift check)."""
        fm, _ = _split_frontmatter(note_path.read_text(encoding="utf-8"))
        value = fm.get("status")
        return str(value) if value is not None else None

    def write_task(self, data: TaskNoteData) -> Path:
        """Full deterministic materialize (create-or-overwrite). The
        filename is stable across title renames — an existing note is found
        by id8 and its filename reused; only a brand-new note is named from
        the current title.

        Archive-aware: the target directory follows ``data.archive_year``. An
        existing note is looked up in the target directory first (the common
        no-op case), falling back to a full ``Tasks/``+``Archive/`` scan — if
        that finds it somewhere else (an archival move, or data now disagrees
        with where the note currently sits), the stale copy is removed after
        the new one is written. This is the one place both the janitor's
        archival pass and ``rebuild`` route through, so they can't drift.
        """
        id8 = _id8(data.id)
        directory = self._task_directory(data)
        directory.mkdir(parents=True, exist_ok=True)
        existing = _find_by_id8(directory, id8) or self.find_task_note(data.id)
        filename = (
            existing.name if existing else f"{_safe_title(data.title)} ({id8}).md"
        )
        path = directory / filename
        path.write_text(
            _render_note(_task_frontmatter(data, id8), "\n".join(_task_body(data))),
            encoding="utf-8",
        )
        if existing is not None and existing != path:
            existing.unlink()
        return path

    def existing_narrative(self, project_slug: str, task_id: str) -> str | None:
        """Read back an existing note's ``## Narrative`` section so a rebuild
        never clobbers Auditor-authored prose (it isn't derivable from DB
        state). None when the note doesn't exist yet or still carries the
        deterministic placeholder. Checks the live ``project_slug`` location
        first, falling back to a full scan (the note may already be archived)."""
        id8 = _id8(task_id)
        existing = _find_by_id8(
            self._tasks_root() / (project_slug or "unassigned"), id8
        ) or self.find_task_note(task_id)
        if existing is None:
            return None
        _, body = _split_frontmatter(existing.read_text(encoding="utf-8"))
        marker = "## Narrative"
        idx = body.find(marker)
        if idx == -1:
            return None
        narrative = body[idx + len(marker) :].strip()
        return narrative if narrative and narrative != _NARRATIVE_PLACEHOLDER else None

    def touch_task_frontmatter(
        self,
        *,
        task_id: str,
        status: str,
        team: str,
        pr_number: int | None,
        pr_url: str | None,
    ) -> bool:
        """Cheap status-transition touch: patch frontmatter keys in place on
        an EXISTING note, never rewriting the body/links. No-op (returns
        False) when the note hasn't been materialized yet — full
        materialization happens at Auditor curation / CLI rebuild, per the
        vault's event-driven freshness model, so this never invents content
        it doesn't have (no extra queries — just the fields on the row)."""
        path = self.find_task_note(task_id)
        if path is None:
            return False
        fm, body = _split_frontmatter(path.read_text(encoding="utf-8"))
        fm["status"] = status
        fm["team"] = team
        fm["pr"] = pr_url or (f"#{pr_number}" if pr_number else None)
        fm["tags"] = [f"status/{status}", f"team/{team}"]
        path.write_text(_render_note(fm, body), encoding="utf-8")
        return True

    # --- journals ----------------------------------------------------------- #

    def write_journal_entry(self, data: JournalNoteData) -> Path:
        """One file per entry (immutable once written — always safe to
        re-render the same entry id in place)."""
        directory = self._journals_root() / data.agent_slug
        directory.mkdir(parents=True, exist_ok=True)
        id8 = _id8(data.entry_id)
        date_str = data.timestamp.strftime("%Y-%m-%d")
        existing = _find_by_id8(directory, id8)
        filename = (
            existing.name
            if existing
            else f"{date_str} {_safe_title(data.title)} ({id8}).md"
        )
        path = directory / filename

        frontmatter = {
            "aliases": [id8],
            "agent": data.agent_slug,
            "scope": data.scope,
            "date": date_str,
        }
        body = [f"# {data.title}", ""]
        if data.task_ref:
            body += [f"Task: {_wikilink(data.task_ref)}", ""]
        body.append((data.content or "").strip())

        path.write_text(_render_note(frontmatter, "\n".join(body)), encoding="utf-8")
        return path

    # --- A2A ---------------------------------------------------------------- #

    def append_a2a_message(self, data: A2AMessageData) -> Path:
        """Per-thread digest file, appended to on every message. Idempotent
        per message id (a marker comment guards against a double-append on
        retry)."""
        directory = self._a2a_root()
        directory.mkdir(parents=True, exist_ok=True)
        id8 = _id8(data.conversation_id)
        participants = sorted({data.from_agent, data.to_agent})
        existing = _find_by_id8(directory, id8)
        if existing is None:
            date_str = data.timestamp.strftime("%Y-%m-%d")
            label = "-".join(participants)
            filename = f"{date_str} {_safe_title(label)} ({id8}).md"
            path = directory / filename
            frontmatter = {
                "aliases": [id8],
                "participants": participants,
                "task": _wikilink(data.task_ref) if data.task_ref else None,
            }
            header = [
                f"# A2A: {label}",
                "",
                "Participants: " + ", ".join(f"[[{p}]]" for p in participants),
            ]
            if data.task_ref:
                header += [f"Task: {_wikilink(data.task_ref)}"]
            path.write_text(
                _render_note(frontmatter, "\n".join(header)), encoding="utf-8"
            )
        else:
            path = existing

        marker = f"<!-- msg:{data.message_id} -->"
        text = path.read_text(encoding="utf-8")
        if marker in text:
            return path
        ts = data.timestamp.strftime("%H:%M:%S")
        block = (
            f"\n**{data.from_agent}** ({ts}) {marker}\n{(data.content or '').strip()}\n"
        )
        with path.open("a", encoding="utf-8") as fh:
            fh.write(block)
        return path

    # --- agents --------------------------------------------------------------- #

    def write_agent(self, data: AgentNoteData) -> Path:
        """Identity hub note; backlinks (Obsidian-native) collect this
        agent's tasks/journal-entries/A2A threads — no explicit index kept
        here."""
        directory = self._agents_root()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{data.slug}.md"
        frontmatter = {"role": data.role, "team": data.team}
        body = f"# {data.name}\n\nRole: {data.role}\nTeam: {data.team or '(none)'}\n"
        path.write_text(_render_note(frontmatter, body), encoding="utf-8")
        return path


def get_vault_writer() -> VaultWriter:
    """Factory reading ``settings.vault_path``. Callers gate on
    ``settings.obsidian_vault_enabled`` themselves (this module has no
    opinion on the flag — it's a pure materializer)."""
    from roboco.config import settings

    return VaultWriter(Path(settings.vault_path))
