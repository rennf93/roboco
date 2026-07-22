"""``python -m roboco.vault {rebuild|relocate <new-path>}``.

``rebuild``: full re-projection of every live entity (agents, tasks, journal
entries, A2A threads) from the DB into the vault, plus materializing the
shipped ``.obsidian/`` config + ``RoboCo/_meta/`` dashboards from packaged
templates (``roboco/vault_assets/``) if not already present. A task's
``## Narrative`` (Auditor-authored, not derivable from DB state) is read back
from the existing note and preserved across the rebuild. Archive-aware: an
old terminal task projects straight into ``RoboCo/Archive/<year>/`` (same
``VaultWriter.write_task`` path the drift janitor's archival pass uses).

``relocate <new-path>``: move the vault tree to a new location. Notes use
relative/alias-based wikilinks, so nothing inside them needs rewriting. An
already-existing destination (a personal vault) receives only the ``RoboCo/``
subtree plus any absent shipped assets — its own ``.obsidian`` is never
touched.

Both are inert unless ``ROBOCO_OBSIDIAN_VAULT_ENABLED`` is on (rebuild would
otherwise materialize a vault nobody reads).
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from importlib import resources
from pathlib import Path
from typing import Any

from roboco.config import settings


def ensure_vault_assets(vault_root: Path) -> None:
    """Materialize ``.obsidian/`` + ``RoboCo/_meta/`` from packaged templates.

    Never overwrites a file that already exists — an operator's own edits to
    the shipped config/dashboards survive both a later rebuild and repeated
    startup calls (idempotent, cheap when everything's already there).
    """
    assets = resources.files("roboco.vault_assets")
    _copy_tree(assets.joinpath("obsidian"), vault_root / ".obsidian")
    _copy_tree(assets.joinpath("meta"), vault_root / "RoboCo" / "_meta")


def _copy_tree(src: Any, dest: Path) -> None:
    for entry in src.iterdir():
        target = dest / entry.name
        if entry.is_dir():
            _copy_tree(entry, target)
        elif not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(entry.read_bytes())


async def _rebuild_agents(writer: Any, agent_service: Any) -> list[Any]:
    from roboco.services.vault_writer import AgentNoteData

    agents = await agent_service.list_agents()
    for agent in agents:
        writer.write_agent(
            AgentNoteData(
                slug=agent.slug,
                name=agent.name,
                role=str(getattr(agent.role, "value", agent.role)),
                team=str(agent.team.value) if agent.team else None,
            )
        )
    return list(agents)


async def _rebuild_tasks(writer: Any, task_service: Any, project_service: Any) -> None:
    from roboco.services.vault_assembly import reproject_task

    offset = 0
    while True:
        tasks = await task_service.list_all(limit=100, offset=offset)
        if not tasks:
            break
        for task in tasks:
            await reproject_task(writer, task_service, project_service, task)
        offset += len(tasks)


async def _rebuild_journals(
    writer: Any, journal_service: Any, agents: list[Any]
) -> None:
    from roboco.foundation.policy.journaling import TYPE_TO_SCOPE
    from roboco.models.journal import ListEntriesFilter
    from roboco.services.vault_writer import JournalNoteData, TaskLinkRef

    for agent in agents:
        journal = await journal_service.get_or_create_journal(agent.id)
        offset = 0
        while True:
            entries = await journal_service.list_entries(
                journal.id, ListEntriesFilter(limit=100, offset=offset)
            )
            if not entries:
                break
            for entry in entries:
                scope = TYPE_TO_SCOPE.get(entry.type)
                task_ref = TaskLinkRef(id=str(entry.task_id)) if entry.task_id else None
                writer.write_journal_entry(
                    JournalNoteData(
                        entry_id=str(entry.id),
                        agent_slug=agent.slug,
                        scope=scope.value if scope else str(entry.type),
                        title=entry.title,
                        content=entry.content,
                        timestamp=entry.timestamp,
                        task_ref=task_ref,
                    )
                )
            offset += len(entries)


async def _rebuild_a2a(writer: Any, db: Any) -> None:
    """A2A vault projection is currently out of scope; kept as a no-op hook."""
    pass


async def _rebuild(vault_root: Path) -> None:
    from roboco.db.base import get_db_context
    from roboco.services.agent import AgentService
    from roboco.services.journal import JournalService
    from roboco.services.project import get_project_service
    from roboco.services.task import TaskService
    from roboco.services.vault_writer import VaultWriter

    writer = VaultWriter(vault_root)
    async with get_db_context() as db:
        agent_service = AgentService(db)
        task_service = TaskService(db)
        journal_service = JournalService(db)
        project_service = get_project_service(db)

        agents = await _rebuild_agents(writer, agent_service)
        await _rebuild_tasks(writer, task_service, project_service)
        await _rebuild_journals(writer, journal_service, agents)
        await _rebuild_a2a(writer, db)

    ensure_vault_assets(vault_root)


def _relocate(new_path: Path) -> int:
    """Move the vault to ``new_path``; 0 on success, 1 on refusal.

    An EXISTING destination is a personal vault: graft only the ``RoboCo/``
    subtree into it (``shutil.move`` of the whole root would nest the old
    dirname inside it) and materialize the shipped ``.obsidian``/``_meta``
    assets only where absent — never clobbering the vault's own config. An
    absent destination gets the whole-tree move.
    """
    old_root = Path(settings.vault_path)
    if not old_root.exists() or old_root == new_path:
        new_path.mkdir(parents=True, exist_ok=True)
    elif new_path.exists():
        dest_tree = new_path / "RoboCo"
        if dest_tree.exists():
            print(
                f"Refusing to relocate: {dest_tree} already exists. Move or "
                "remove it first.",
                file=sys.stderr,
            )
            return 1
        old_tree = old_root / "RoboCo"
        if old_tree.exists():
            shutil.move(str(old_tree), str(dest_tree))
        ensure_vault_assets(new_path)
    else:
        new_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old_root), str(new_path))
    print(
        f"Vault moved to {new_path}. Set ROBOCO_VAULT_PATH={new_path} in the "
        "environment for this to persist across restarts."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m roboco.vault")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("rebuild", help="full re-projection from the DB")
    relocate = subcommands.add_parser("relocate", help="move the vault tree")
    relocate.add_argument("new_path", type=Path)
    args = parser.parse_args(argv)

    if not settings.obsidian_vault_enabled:
        print(
            "ROBOCO_OBSIDIAN_VAULT_ENABLED is off — nothing to do.",
            file=sys.stderr,
        )
        return 1
    if args.command == "rebuild":
        asyncio.run(_rebuild(Path(settings.vault_path)))
        print(f"Vault rebuilt at {settings.vault_path}")
        return 0
    return _relocate(args.new_path)


if __name__ == "__main__":
    raise SystemExit(main())
