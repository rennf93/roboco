"""The Entire trails bridge against the database.

The hooks write outbox rows in the lifecycle's own transaction; the drain
runs them through a stand-in for the Entire CLI that answers every command
with the pinned CLI's recorded output (tests/fixtures/entire_cli/0.12.2,
adapted to this test's ids) and checks every flag it is given against that
CLI's recorded ``--help``. Failures back off, hold their tree, go dead after
the attempt budget and alert the CEO.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.db.tables import (
    EntireTrailChangeTable,
    EntireTrailFindingTable,
    EntireTrailOutboxTable,
    EntireTrailTable,
    NotificationTable,
    TaskTable,
)
from roboco.foundation.policy.content import Severity
from roboco.models import Team
from roboco.models.base import TaskStatus
from roboco.services.entire_credentials import get_entire_credentials_service
from roboco.services.entire_trails import drain
from roboco.services.entire_trails.status import retry_dead, trails_status
from roboco.services.repositories.review_findings import ReviewFindingsRepository
from roboco.utils.converters import require_uuid
from sqlalchemy import select

from tests.integration.entire_trails.support import (
    DEV,
    DEV_BRANCH,
    MAIN_PM,
    REPOSITORY,
    ROOT_BRANCH,
    command,
    flag,
)

if TYPE_CHECKING:
    from tests.integration.entire_trails.support import Bridge


async def _bridged_tree(b: Bridge) -> tuple[TaskTable, TaskTable]:
    """A root cut by the Main PM and a dev leaf two levels under it."""
    root = await b.task()
    await b.cut(root, ROOT_BRANCH, "master", MAIN_PM)
    cell = await b.task(parent=root, team=Team.BACKEND, branch="feature/backend/c")
    leaf = await b.task(parent=cell, team=Team.BACKEND)
    await b.cut(leaf, DEV_BRANCH, "feature/backend/c", DEV)
    return root, leaf


# --- hooks: what the lifecycle queues -----------------------------------------


async def test_the_roots_first_branch_starts_its_trail_with_frozen_create_fields(
    bridge: Bridge,
) -> None:
    root = await bridge.task()
    await bridge.cut(root, ROOT_BRANCH, "master", MAIN_PM)

    trail = await bridge.row(EntireTrailTable, root.id)
    assert trail.entire_project == "gh/acme"
    assert trail.trail_number is None
    assert trail.create_fields["title"] == f"[{str(root.id)[:8]}] Rate limiting"
    assert "Rate-limit the public API." in trail.create_fields["body"]
    assert [r.op for r in await bridge.outbox()] == ["sync_trail"]


async def test_a_dev_branch_is_queued_for_linking_and_an_integration_branch_is_not(
    bridge: Bridge,
) -> None:
    root, leaf = await _bridged_tree(bridge)

    rows = await bridge.outbox()
    assert [r.op for r in rows] == ["sync_trail", "link"]
    assert rows[1].payload == {
        "task_id": str(leaf.id),
        "repository": REPOSITORY,
        "branch": DEV_BRANCH,
        "base": "feature/backend/c",
    }
    assert all(r.root_task_id == root.id for r in rows)


@pytest.mark.parametrize("unbridged", ["flag_off", "project_not_opted_in", "gitea"])
async def test_nothing_is_queued_for_a_project_that_is_not_bridged(
    bridge: Bridge, monkeypatch: pytest.MonkeyPatch, unbridged: str
) -> None:
    if unbridged == "flag_off":
        monkeypatch.setattr(settings, "entire_trails_enabled", False)
    elif unbridged == "project_not_opted_in":
        bridge.project.entire_trails_enabled = False
    else:
        bridge.project.git_url = "https://gitea.acme.test/acme/app.git"
        bridge.project.git_provider = "gitea"
    root = await bridge.task()
    await bridge.cut(root, ROOT_BRANCH, "master", MAIN_PM)

    assert await bridge.row(EntireTrailTable, root.id) is None
    assert await bridge.outbox() == []


async def test_a_tree_already_running_when_its_project_opted_in_stays_unbridged(
    bridge: Bridge,
) -> None:
    root = await bridge.task(branch=ROOT_BRANCH)
    leaf = await bridge.task(parent=root, team=Team.BACKEND)
    await bridge.cut(leaf, DEV_BRANCH, ROOT_BRANCH, DEV)

    assert await bridge.row(EntireTrailTable, root.id) is None
    assert await bridge.outbox() == []


async def test_a_megatask_is_one_trail_started_by_its_first_root_subtask(
    bridge: Bridge,
) -> None:
    umbrella = await bridge.task(batch_id=uuid4())
    first = await bridge.task(parent=umbrella)
    second = await bridge.task(parent=umbrella)
    await bridge.cut(first, "feature/main_pm/umb--first", "master", MAIN_PM)
    await bridge.cut(second, "feature/main_pm/umb--second", "master", MAIN_PM)

    assert await bridge.row(EntireTrailTable, umbrella.id) is not None
    assert await bridge.row(EntireTrailTable, first.id) is None
    assert {r.root_task_id for r in await bridge.outbox()} == {umbrella.id}


async def test_findings_are_queued_with_their_text_rendered_once(
    bridge: Bridge,
) -> None:
    root, leaf = await _bridged_tree(bridge)
    leaf.branch_name = DEV_BRANCH
    rows = await bridge.findings(leaf, Severity.BLOCKER, Severity.NIT)

    adds = [r for r in await bridge.outbox() if r.op == "finding_add"]
    assert [a.payload["finding_id"] for a in adds] == [str(r.id) for r in rows]
    assert [a.payload["severity"] for a in adds] == ["high", "low"]
    assert adds[0].payload["branch"] == DEV_BRANCH
    assert adds[0].payload["repository"] == REPOSITORY
    assert adds[0].payload["file"] == "roboco/api/limits.py"
    assert str(rows[0].id) in adds[0].payload["body"]
    assert all(a.root_task_id == root.id for a in adds)


async def test_a_finding_on_an_unbridged_tree_is_not_queued(bridge: Bridge) -> None:
    root = await bridge.task(branch=ROOT_BRANCH)
    await bridge.findings(root, Severity.MAJOR)
    assert await bridge.outbox() == []


# --- drain: what reaches Entire ------------------------------------------------


async def test_the_drain_creates_the_trail_links_and_adds_findings_in_order(
    bridge: Bridge,
) -> None:
    root, leaf = await _bridged_tree(bridge)
    leaf.branch_name = DEV_BRANCH
    (finding,) = await bridge.findings(leaf, Severity.MAJOR)

    await drain.drain_due()

    assert [command(c) for c in bridge.replay.calls] == [
        "trail create",
        "trail update",
        "trail link",
        "trail show",
        "trail finding add",
    ]
    create = bridge.replay.calls[0]
    assert flag(create, "idempotency-key") == f"roboco-trail:{root.id}"
    assert flag(create, "project") == "gh/acme"
    trail = await bridge.row(EntireTrailTable, root.id)
    assert (trail.trail_number, trail.trail_url) == (
        41,
        "https://entire.com/gh/acme/-/trails/41",
    )
    assert trail.synced_at is not None
    mapped = await bridge.row(EntireTrailFindingTable, finding.id)
    assert mapped.entire_finding_id == bridge.replay.findings[0]["id"]
    assert (mapped.repository, mapped.branch) == (REPOSITORY, DEV_BRANCH)
    async with bridge.factory() as s:
        change = (await s.execute(select(EntireTrailChangeTable))).scalar_one()
    assert (change.branch, change.change_number, change.task_id) == (
        DEV_BRANCH,
        7,
        leaf.id,
    )
    assert await bridge.outbox() == []


async def test_a_finding_s_life_reaches_its_trail_finding(bridge: Bridge) -> None:
    _root, leaf = await _bridged_tree(bridge)
    leaf.branch_name = DEV_BRANCH
    fixed, waived = await bridge.findings(leaf, Severity.MAJOR, Severity.NIT)
    await drain.drain_due()
    repo = ReviewFindingsRepository(bridge.session)

    await repo.mark_addressed(
        require_uuid(leaf.id), str(fixed.id), commit="abc1234", note="per client"
    )
    await repo.mark_verified([fixed.id], verified_by="be-qa")
    await repo.mark_waived(waived.id, "cosmetic, tracked elsewhere")
    await bridge.session.commit()
    await drain.drain_due()

    update = bridge.replay.made("trail finding update")[0]
    assert str(flag(update, "body")).endswith("Addressed in abc1234: per client")
    resolve = bridge.replay.made("trail finding resolve")[0]
    assert flag(resolve, "message") == "verified by be-qa at abc1234"
    dismiss = bridge.replay.made("trail finding dismiss")[0]
    assert flag(dismiss, "message") == "waived: cosmetic, tracked elsewhere"
    assert bridge.replay.findings[0]["id"] in resolve
    assert bridge.replay.findings[1]["id"] in dismiss


async def test_the_drain_sends_the_text_rendered_when_the_finding_was_queued(
    bridge: Bridge,
) -> None:
    """A retry must send byte-identical fields (Entire refuses a client id
    reused with others), so the text is the queued one, never a re-render."""
    _root, leaf = await _bridged_tree(bridge)
    leaf.branch_name = DEV_BRANCH
    (finding,) = await bridge.findings(leaf, Severity.MAJOR)
    queued = next(r for r in await bridge.outbox() if r.op == "finding_add")
    finding.actual = "a 500 on request 101"
    await bridge.session.commit()

    await drain.drain_due()

    (add,) = bridge.replay.made("trail finding add")
    assert flag(add, "body") == queued.payload["body"]
    assert "a 500" not in str(flag(add, "body"))


async def test_a_failed_operation_backs_off_and_holds_its_tree(bridge: Bridge) -> None:
    _root, leaf = await _bridged_tree(bridge)
    leaf.branch_name = DEV_BRANCH
    await bridge.findings(leaf, Severity.MAJOR)
    bridge.replay.fail("trail link", "API error: Service Unavailable (status 503)")

    await drain.drain_due()

    link, add = [r for r in await bridge.outbox() if r.op in ("link", "finding_add")]
    assert (link.attempts, link.dead) == (1, False)
    assert "status 503" in str(link.last_error)
    assert link.next_attempt_at > datetime.now(UTC) + timedelta(seconds=20)
    assert add.attempts == 0
    assert bridge.replay.made("trail finding add") == []


async def test_an_operation_that_keeps_failing_goes_dead_and_alerts_the_ceo_once(
    bridge: Bridge, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "entire_trails_max_attempts", 1)
    await _bridged_tree(bridge)
    bridge.replay.fail(
        "trail link", "API error: Change already belongs to another Trail"
    )

    await drain.drain_due()
    await drain.drain_due()

    (link,) = [r for r in await bridge.outbox() if r.op == "link"]
    assert link.dead
    assert len(bridge.replay.made("trail link")) == 1
    async with bridge.factory() as s:
        alerts = (
            (
                await s.execute(
                    select(NotificationTable).where(
                        NotificationTable.subject.like("Entire trails:%")
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(alerts) == 1
    assert "another Trail" in alerts[0].body
    status = await trails_status(bridge.session)
    assert (status.dead, status.pending) == (1, 0)
    assert "another Trail" in str(status.last_error)

    assert await retry_dead(bridge.session) == 1
    await bridge.session.commit()
    await drain.drain_due()
    assert [r.op for r in await bridge.outbox()] == []


async def test_a_retried_finding_add_finds_the_finding_entire_already_has(
    bridge: Bridge,
) -> None:
    """The add reached Entire but its result was lost: the retry looks the
    finding up by its client id (the ledger id) instead of adding it twice."""
    _root, leaf = await _bridged_tree(bridge)
    leaf.branch_name = DEV_BRANCH
    (finding,) = await bridge.findings(leaf, Severity.MAJOR)
    bridge.replay.findings.append(
        {
            "id": "01ENTIREALREADYTHERE0000001",
            "clientId": str(finding.id),
            "status": "open",
        }
    )
    async with bridge.factory() as s:
        row = (
            await s.execute(
                select(EntireTrailOutboxTable).where(
                    EntireTrailOutboxTable.op == "finding_add"
                )
            )
        ).scalar_one()
        row.attempts = 1
        await s.commit()

    await drain.drain_due()

    assert bridge.replay.made("trail finding add") == []
    mapped = await bridge.row(EntireTrailFindingTable, finding.id)
    assert mapped.entire_finding_id == "01ENTIREALREADYTHERE0000001"


async def test_nothing_runs_without_a_token(bridge: Bridge) -> None:
    await _bridged_tree(bridge)
    await get_entire_credentials_service(bridge.session).set_token("")
    await bridge.session.commit()

    assert await drain.drain_due() == 0
    assert bridge.replay.calls == []
    assert [r.op for r in await bridge.outbox()] == ["sync_trail", "link"]


async def test_reconcile_refreshes_a_changed_tree_and_closes_an_ended_one(
    bridge: Bridge,
) -> None:
    root, leaf = await _bridged_tree(bridge)
    await drain.drain_due()
    assert await drain.reconcile_trails() == 0

    leaf.title = "Rate limiting, per client"
    await bridge.session.commit()
    await drain.drain_due()
    update = bridge.replay.made("trail update")[-1]
    assert "Rate limiting, per client" in str(flag(update, "body"))

    root.status = TaskStatus.COMPLETED
    await bridge.session.commit()
    await drain.drain_due()

    close = bridge.replay.made("trail update")[-1]
    assert "--status=closed" in close
    assert "## Outcome" in str(flag(close, "body"))
    assert (await bridge.row(EntireTrailTable, root.id)).closed_at is not None
    await bridge.findings(leaf, Severity.MAJOR)
    assert await bridge.outbox() == []


# --- inline: before the PR, before the merge -----------------------------------


async def test_link_before_pr_links_now_and_drops_the_queued_link(
    bridge: Bridge,
) -> None:
    _root, leaf = await _bridged_tree(bridge)

    await drain.link_before_pr(
        require_uuid(leaf.id),
        bridge.project,
        branch=DEV_BRANCH,
        base="feature/backend/c",
    )

    assert [command(c) for c in bridge.replay.calls] == [
        "trail create",
        "trail link",
        "trail show",
    ]
    assert [r.op for r in await bridge.outbox()] == ["sync_trail"]
    await drain.drain_due()
    assert len(bridge.replay.made("trail link")) == 1


async def test_an_integration_branch_links_before_its_pr(bridge: Bridge) -> None:
    root = await bridge.task()
    await bridge.cut(root, ROOT_BRANCH, "master", MAIN_PM)

    await drain.link_before_pr(
        require_uuid(root.id), bridge.project, branch=ROOT_BRANCH, base="master"
    )

    assert bridge.replay.links == [(REPOSITORY, ROOT_BRANCH)]


async def test_a_failed_link_before_pr_is_queued_not_raised(bridge: Bridge) -> None:
    root = await bridge.task()
    await bridge.cut(root, ROOT_BRANCH, "master", MAIN_PM)
    bridge.replay.fail("trail link", "API error: Bad Gateway (status 502)")

    await drain.link_before_pr(
        require_uuid(root.id), bridge.project, branch=ROOT_BRANCH, base="master"
    )

    queued = [r for r in await bridge.outbox() if r.op == "link"]
    assert [r.payload["branch"] for r in queued] == [ROOT_BRANCH]


async def test_the_ceo_s_approval_lands_on_the_root_change_before_the_merge(
    bridge: Bridge,
) -> None:
    root, leaf = await _bridged_tree(bridge)
    await drain.link_before_pr(
        require_uuid(root.id), bridge.project, branch=ROOT_BRANCH, base="master"
    )

    await drain.approve_before_merge(
        require_uuid(root.id),
        bridge.project,
        branch=ROOT_BRANCH,
        message="Approved by the CEO",
    )
    await drain.approve_before_merge(
        require_uuid(leaf.id), bridge.project, branch=DEV_BRANCH, message="not a root"
    )

    (approve,) = bridge.replay.made("trail approve")
    assert flag(approve, "branch") == ROOT_BRANCH
    assert flag(approve, "message") == "Approved by the CEO"
