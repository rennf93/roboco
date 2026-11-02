"""The trails bridge against the real Entire service, end to end.

Opt-in: run with ``ROBOCO_ENTIRE_TRAILS_LIVE=1`` and

- ``ROBOCO_ENTIRE_TRAILS_LIVE_REPO``: the GitHub repository (``owner/repo``)
  whose Entire project receives a throwaway trail (closed by the test);
- ``ROBOCO_ENTIRE_TRAILS_LIVE_ROOT_BRANCH`` / ``..._DEV_BRANCH``: two
  branches already pushed there with no commits of their own (so Entire
  opens no PR for them), deleted by whoever pushed them;
- an Entire login on this machine (``ENTIRE_TOKEN_STORE=file`` and the
  CLI on PATH, or ``ROBOCO_ENTIRE_TRAILS_LIVE_CLI``). The bridge passes its
  token as ``ENTIRE_TOKEN``; this test swaps that one line of the
  environment for the stored login, since a token cannot be read back from
  the store. The token path itself is covered by the unit tests.

One task tree goes through its whole life (trail, two links, findings
added / addressed / verified / waived, the CEO's approval, the close); after
it, a checker compares ``trail show`` / ``finding list`` / ``trail
approvals`` with the database and asserts Entire opened no trail of its own.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from roboco.db.tables import (
    EntireTrailChangeTable,
    EntireTrailFindingTable,
    EntireTrailTable,
)
from roboco.foundation.policy.content import Severity
from roboco.models import Team
from roboco.models.base import TaskStatus
from roboco.services.entire_trails import cli, drain
from roboco.services.repositories.review_findings import ReviewFindingsRepository
from roboco.utils.converters import require_uuid
from sqlalchemy import select

from tests.integration.entire_trails.support import DEV, MAIN_PM

if TYPE_CHECKING:
    from pathlib import Path

    from tests.integration.entire_trails.support import Bridge

pytestmark = pytest.mark.skipif(
    os.environ.get("ROBOCO_ENTIRE_TRAILS_LIVE") != "1",
    reason="live Entire trails test: set ROBOCO_ENTIRE_TRAILS_LIVE=1 (see docstring)",
)


def _env(name: str) -> str:
    value = os.environ.get(name, "")
    assert value, f"{name} is required for the live test"
    return value


def _stored_login_env(token: str, home: Path) -> dict[str, str]:
    """The bridge's environment with the machine's stored Entire login in
    place of ``ENTIRE_TOKEN`` (see the module docstring)."""
    _ = (token, home)
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": os.environ["HOME"],
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "ENTIRE_TOKEN_STORE": "file",
        "ENTIRE_TELEMETRY_OPTOUT": "1",
    }


def _entire(binary: str, *args: str) -> dict[str, Any]:
    """The checker's own read of Entire, straight from the CLI rather than
    through the bridge under test."""
    env = _stored_login_env("", cli.entire_home())
    out = subprocess.run(
        [binary, *args],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return dict(json.loads(out.stdout))


@pytest.fixture
def live(
    bridge: Bridge, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, str]:
    binary = os.environ.get("ROBOCO_ENTIRE_TRAILS_LIVE_CLI") or shutil.which("entire")
    assert binary, "the Entire CLI is not on PATH"
    owner, repo = _env("ROBOCO_ENTIRE_TRAILS_LIVE_REPO").split("/", 1)
    bridge.project.git_url = f"https://github.com/{owner}/{repo}.git"
    monkeypatch.setattr(cli, "ENTIRE_CLI", binary)
    monkeypatch.setattr(cli, "_cli_env", _stored_login_env)
    monkeypatch.setattr(cli.settings, "private_data_root", str(tmp_path))
    monkeypatch.setattr(drain, "run_entire", cli.run_entire)
    return {
        "binary": binary,
        "project": f"gh/{owner}",
        "repository": f"gh/{owner}/{repo}",
        "repo_name": repo,
        "root_branch": _env("ROBOCO_ENTIRE_TRAILS_LIVE_ROOT_BRANCH"),
        "dev_branch": _env("ROBOCO_ENTIRE_TRAILS_LIVE_DEV_BRANCH"),
    }


async def test_a_task_tree_lives_its_whole_life_on_a_real_trail(
    bridge: Bridge, live: dict[str, str]
) -> None:
    started = datetime.now(UTC)
    root = await bridge.task(title="[live test] trails bridge: safe to ignore")
    await bridge.cut(root, live["root_branch"], "master", MAIN_PM)
    cell = await bridge.task(parent=root, team=Team.BACKEND, branch="unused")
    leaf = await bridge.task(parent=cell, team=Team.BACKEND, title="[live test] leaf")
    await bridge.cut(leaf, live["dev_branch"], "master", DEV)
    fixed, waived = await bridge.findings(leaf, Severity.MAJOR, Severity.NIT)

    await drain.drain_due()
    repo = ReviewFindingsRepository(bridge.session)
    await repo.mark_addressed(
        require_uuid(leaf.id), str(fixed.id), commit="abc1234", note="fixed"
    )
    await repo.mark_verified([fixed.id], verified_by="be-qa")
    await repo.mark_waived(waived.id, "cosmetic")
    await bridge.session.commit()
    await drain.drain_due()
    await drain.link_before_pr(
        require_uuid(root.id), bridge.project, branch=live["root_branch"], base="master"
    )
    await drain.approve_before_merge(
        require_uuid(root.id),
        bridge.project,
        branch=live["root_branch"],
        message="live approval",
    )
    root.status = TaskStatus.COMPLETED
    await bridge.session.commit()
    await drain.drain_due()

    assert await bridge.outbox() == []
    trail = await bridge.row(EntireTrailTable, root.id)
    number = str(trail.trail_number)
    shown = _entire(
        live["binary"],
        "trail",
        "show",
        number,
        f"--project={live['project']}",
        "--json",
    )
    assert shown["status"] == "closed"
    assert str(root.id) in shown["body"]
    changes = {c["branch"]: c for c in shown["changes"]}
    assert set(changes) == {live["root_branch"], live["dev_branch"]}
    async with bridge.factory() as s:
        mapped_changes = (
            (await s.execute(select(EntireTrailChangeTable))).scalars().all()
        )
        mapped_findings = {
            str(f.review_finding_id): f
            for f in (await s.execute(select(EntireTrailFindingTable))).scalars().all()
        }
    for change in mapped_changes:
        assert changes[change.branch]["number"] == change.change_number
        assert changes[change.branch]["repository"] == live["repo_name"]

    listed = _entire(
        live["binary"],
        "trail",
        "finding",
        "list",
        number,
        f"--repo={live['repository']}",
        f"--branch={live['dev_branch']}",
        "--status=any",
        "--freshness=any",
        "--include-dismissed",
        "--json",
    )
    by_client = {f["clientId"]: f for f in listed["findings"]}
    assert set(by_client) == {str(fixed.id), str(waived.id)}
    for client_id, finding in by_client.items():
        assert mapped_findings[client_id].entire_finding_id == finding["id"]
    assert by_client[str(fixed.id)]["status"] == "resolved"
    assert by_client[str(fixed.id)]["statusReason"] == "verified by be-qa at abc1234"
    assert "Addressed in abc1234: fixed" in by_client[str(fixed.id)]["body"]
    assert by_client[str(waived.id)]["status"] == "dismissed"

    approvals = _entire(
        live["binary"],
        "trail",
        "approvals",
        number,
        f"--repo={live['repository']}",
        f"--branch={live['root_branch']}",
        "--json",
    )
    assert [a["body"] for a in approvals["approvals"]] == ["live approval"]

    listed_trails = _entire(
        live["binary"], "trail", "list", f"--project={live['project']}", "--json"
    )
    created_since = [
        t
        for t in listed_trails["items"]
        if datetime.fromisoformat(str(t["createdAt"]).replace("Z", "+00:00")) >= started
    ]
    assert [t["number"] for t in created_since] == [trail.trail_number]
