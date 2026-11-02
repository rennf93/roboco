"""Shared pieces of the Entire trails integration tests: the replayed CLI
(the pinned CLI's recorded answers, every flag checked against its recorded
help) and a handle on the seeded project, its tasks and its outbox."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from roboco.db.tables import (
    AgentTable,
    EntireTrailOutboxTable,
    ProjectTable,
    TaskTable,
)
from roboco.foundation.identity import agent_for_slug
from roboco.foundation.policy.content import Finding, Severity
from roboco.models import Team
from roboco.models.base import TaskNature, TaskStatus, TaskType
from roboco.services.entire_trails import hooks
from roboco.services.entire_trails.cli import EntireCliError, EntireCliResult
from roboco.services.repositories.review_findings import ReviewFindingsRepository
from roboco.utils.converters import require_uuid
from sqlalchemy import select

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_RECORDED = Path(__file__).resolve().parents[2] / "fixtures" / "entire_cli" / "0.12.2"
REPOSITORY = "gh/acme/app"
ROOT_BRANCH = "feature/main_pm/root0001"
DEV_BRANCH = "feature/backend/root0001--cell0002--leaf0003"
MAIN_PM = agent_for_slug("main-pm").uuid
DEV = agent_for_slug("be-dev-1").uuid


def recorded(name: str) -> Any:
    text = (_RECORDED / name).read_text()
    return json.loads(text) if name.endswith(".json") else text


def flag(argv: Sequence[str], name: str) -> str | None:
    prefix = f"--{name}="
    return next((a[len(prefix) :] for a in argv if a.startswith(prefix)), None)


def command(argv: Sequence[str]) -> str:
    words = [a for a in argv if not a.startswith("-")]
    return " ".join(words[:3] if words[1] == "finding" else words[:2])


@dataclass
class ReplayEntire:
    """Stands in for ``run_entire``: the pinned CLI's recorded answer to each
    command, every flag checked against its recorded help."""

    calls: list[tuple[str, ...]] = field(default_factory=list)
    failures: dict[str, list[str]] = field(default_factory=dict)
    findings: list[dict[str, Any]] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)

    def fail(self, name: str, message: str, times: int = 1) -> None:
        self.failures.setdefault(name, []).extend([message] * times)

    def made(self, name: str) -> list[tuple[str, ...]]:
        return [c for c in self.calls if command(c) == name]

    def _check_flags(self, argv: Sequence[str]) -> None:
        help_text = (
            _RECORDED / f"help-{command(argv).replace(' ', '-')}.txt"
        ).read_text()
        for arg in argv:
            if arg.startswith("--"):
                option = arg.split("=", 1)[0]
                assert re.search(rf"\s{re.escape(option)}\b", help_text), (
                    f"{option} is not a flag of entire {command(argv)} (0.12.2)"
                )

    async def __call__(
        self, args: Sequence[str], *, token: str, timeout: float | None = None
    ) -> EntireCliResult:
        _ = timeout
        assert token == "entire-token"
        argv = tuple(args)
        self._check_flags(argv)
        self.calls.append(argv)
        name = command(argv)
        pending = self.failures.get(name)
        if pending:
            raise EntireCliError(pending.pop(0))
        return EntireCliResult(0, self._answer(name, argv), "")

    def _answer(self, name: str, argv: tuple[str, ...]) -> str:
        if name == "trail create":
            created = recorded("trail-create.json")
            created.update(number=41, url="https://entire.com/gh/acme/-/trails/41")
            return json.dumps(created)
        if name == "trail link":
            self.links.append((str(flag(argv, "repo")), str(flag(argv, "branch"))))
            return json.dumps(recorded("trail-link.json"))
        if name == "trail show":
            shown = recorded("trail-show.json")
            shown["changes"] = [
                {
                    **shown["changes"][0],
                    "repository": repo.rsplit("/", 1)[-1],
                    "branch": branch,
                    "number": 7 + i,
                }
                for i, (repo, branch) in enumerate(self.links)
            ]
            return json.dumps(shown)
        if name == "trail finding add":
            added = recorded("finding-add.json")
            added.update(
                id=f"01ENTIREFINDING{len(self.findings):011d}",
                clientId=flag(argv, "client-id"),
                body=flag(argv, "body"),
            )
            self.findings.append(added)
            return json.dumps(added)
        if name == "trail finding list":
            listed = recorded("finding-list.json")
            listed.update(findings=self.findings, has_more=False)
            return json.dumps(listed)
        answers = {
            "trail update": "trail-update.json",
            "trail finding update": "finding-update.json",
            "trail finding resolve": "finding-resolve.txt",
            "trail finding dismiss": "finding-dismiss.txt",
            "trail approve": "trail-approve.txt",
        }
        answer = recorded(answers[name])
        return json.dumps(answer) if isinstance(answer, dict) else answer


@dataclass
class Bridge:
    session: AsyncSession
    factory: async_sessionmaker[AsyncSession]
    replay: ReplayEntire
    project: ProjectTable
    agent: AgentTable

    async def task(
        self,
        *,
        parent: TaskTable | None = None,
        team: Team = Team.MAIN_PM,
        branch: str | None = None,
        title: str = "Rate limiting",
        batch_id: UUID | None = None,
    ) -> TaskTable:
        task = TaskTable(
            id=uuid4(),
            title=title,
            description="Rate-limit the public API.",
            acceptance_criteria=["429 after 100 requests a minute"],
            status=TaskStatus.IN_PROGRESS,
            priority=1,
            task_type=TaskType.CODE,
            nature=TaskNature.TECHNICAL,
            project_id=self.project.id,
            created_by=self.agent.id,
            team=team,
            parent_task_id=parent.id if parent is not None else None,
            branch_name=branch,
            batch_id=batch_id,
        )
        self.session.add(task)
        await self.session.flush()
        return task

    async def cut(
        self, task: TaskTable, branch: str, base: str, claimed_by: UUID
    ) -> None:
        task.branch_name = branch
        await hooks.branch_created(
            self.session, task, self.project, hooks.BranchCut(branch, base, claimed_by)
        )
        await self.session.commit()

    async def outbox(self) -> list[EntireTrailOutboxTable]:
        async with self.factory() as s:
            result = await s.execute(
                select(EntireTrailOutboxTable).order_by(EntireTrailOutboxTable.id)
            )
            return list(result.scalars().all())

    async def row(self, table: Any, key: Any) -> Any:
        async with self.factory() as s:
            return await s.get(table, key)

    async def findings(self, task: TaskTable, *severities: Severity) -> list[Any]:
        rows = await ReviewFindingsRepository(self.session).insert_many(
            task_id=require_uuid(task.id),
            origin="qa",
            round=1,
            author_slug="be-qa",
            findings=[
                Finding(
                    file="roboco/api/limits.py",
                    line=12,
                    severity=s,
                    criterion="429 after 100 requests a minute",
                    expected="a 429 on request 101",
                    actual="a 200 on request 101",
                    fix="count per client, not per process",
                )
                for s in severities
            ],
        )
        await self.session.commit()
        return rows
