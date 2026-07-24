"""External-PR author/fork classification — the inbound trust decision.

``_is_external_pr`` decides whether an open PR was authored by the org itself
or by an outside contributor. It must default to *external* (the cautious
side) for anything it does not positively recognize as internal.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import roboco.runtime.orchestrator as orch_mod
from roboco.runtime.orchestrator import AgentOrchestrator


def _proj(slug: str, git_url: str | None) -> SimpleNamespace:
    return SimpleNamespace(slug=slug, git_url=git_url)


def test_projects_one_per_repo_collapses_monorepo() -> None:
    # Three cell-projects all pointing at the same repo (a monorepo product)
    # collapse to ONE canonical project; a genuinely separate repo is kept.
    projects = [
        _proj("roboco-uix", "https://github.com/rennf93/roboco.git"),
        _proj("roboco-api", "https://github.com/rennf93/roboco.git"),
        _proj("roboco-panel", "https://github.com/rennf93/roboco.git"),
        _proj("other", "https://github.com/rennf93/other-repo.git"),
    ]
    out = AgentOrchestrator._projects_one_per_repo(projects)
    slugs = [p.slug for p in out]
    # one per distinct repo; canonical pick is deterministic (first by slug).
    assert slugs == ["other", "roboco-api"]


def test_projects_one_per_repo_normalizes_and_skips_repoless() -> None:
    projects = [
        _proj("a", "https://github.com/rennf93/roboco"),  # no .git
        _proj("b", "https://github.com/rennf93/roboco.git/"),  # .git + slash
        _proj("coordination", None),  # product/coordination project, no repo
    ]
    out = AgentOrchestrator._projects_one_per_repo(projects)
    # a & b are the same repo; coordination (no git_url) is skipped.
    assert [p.slug for p in out] == ["a"]


@pytest.mark.parametrize(
    ("pr", "expected"),
    [
        # A fork head is always external, regardless of association.
        ({"is_fork": True, "author_association": "OWNER"}, True),
        # Same-repo branch from a trusted association is internal (the org).
        ({"is_fork": False, "author_association": "OWNER"}, False),
        ({"is_fork": False, "author_association": "MEMBER"}, False),
        ({"is_fork": False, "author_association": "COLLABORATOR"}, False),
        ({"is_fork": False, "author_association": "member"}, False),  # case-insensitive
        # Outside associations are external even on a same-repo branch.
        ({"is_fork": False, "author_association": "CONTRIBUTOR"}, True),
        ({"is_fork": False, "author_association": "FIRST_TIME_CONTRIBUTOR"}, True),
        ({"is_fork": False, "author_association": "NONE"}, True),
        ({"is_fork": False, "author_association": None}, True),
        # Unknown/empty shape defaults to external (cautious).
        ({}, True),
    ],
)
def test_is_external_pr(pr: dict[str, object], *, expected: bool) -> None:
    assert AgentOrchestrator._is_external_pr(pr) is expected


@pytest.mark.parametrize(
    ("pr", "allowlist", "expected"),
    [
        # Empty allowlist -> every external PR is reviewed (read-only, safe).
        ({"user_login": "corey"}, set(), True),
        # Non-empty allowlist gates by GitHub login (case-insensitive).
        ({"user_login": "corey"}, {"corey"}, True),
        ({"user_login": "Corey"}, {"corey"}, True),
        ({"user_login": "mallory"}, {"corey"}, False),
        ({"user_login": None}, {"corey"}, False),
        ({}, {"corey"}, False),
    ],
)
def test_pr_author_allowed(
    pr: dict[str, object], allowlist: set[str], *, expected: bool
) -> None:
    assert AgentOrchestrator._pr_author_allowed(pr, allowlist) is expected


@pytest.mark.parametrize(
    ("quick_context", "expected"),
    [
        ("external_pr_supersede pr=42 review=abc", 42),
        ("external_pr_supersede pr=7 review=abc closed=1", 7),
        ("external_pr_supersede pr=50 review=abc", 50),  # not confused by pr=5
        ("", None),
        ("no marker here", None),
        ("external_pr_supersede pr=notanint review=abc", None),
        # A CEO note on a later line must not shadow the marker line's pr=.
        ("external_pr_supersede pr=11 review=abc\nceo_approval_notes: pr=99 ok", 11),
    ],
)
def test_parse_supersede_pr(quick_context: str, expected: int | None) -> None:
    assert AgentOrchestrator._parse_supersede_pr(quick_context) == expected


# ---------------------------------------------------------------------------
# _ingest_pr_if_reviewable — the external/internal review decision (#3)
# ---------------------------------------------------------------------------


def _orch() -> AgentOrchestrator:
    """A bare orchestrator — the method only uses staticmethods + the service."""
    return object.__new__(AgentOrchestrator)


def _svc(*, owns_branch: bool = False) -> MagicMock:
    svc = MagicMock()
    svc.ingest_external_pr = AsyncMock(return_value=object())  # truthy "created"
    svc.active_task_owns_branch = AsyncMock(return_value=owns_branch)
    return svc


@pytest.mark.asyncio
async def test_ingest_external_fork_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(orch_mod.settings, "external_pr_enabled", True)
    svc = _svc()
    pr = {"number": 5, "is_fork": True, "user_login": "corey", "head_ref": "x"}
    ok = await _orch()._ingest_pr_if_reviewable(
        svc, SimpleNamespace(id=uuid4()), pr, uuid4(), set()
    )
    assert ok is True
    assert svc.ingest_external_pr.await_args.kwargs["source"] == "external_pr"


@pytest.mark.asyncio
async def test_skip_external_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orch_mod.settings, "external_pr_enabled", False)
    svc = _svc()
    pr = {"number": 5, "is_fork": True, "user_login": "corey"}
    ok = await _orch()._ingest_pr_if_reviewable(
        svc, SimpleNamespace(id=uuid4()), pr, uuid4(), set()
    )
    assert ok is False
    svc.ingest_external_pr.assert_not_awaited()


@pytest.mark.asyncio
async def test_ingest_internal_off_task_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A non-fork org PR no live task owns is an off-task-flow PR → review it.
    monkeypatch.setattr(orch_mod.settings, "internal_pr_enabled", True)
    svc = _svc(owns_branch=False)
    pr = {
        "number": 9,
        "is_fork": False,
        "author_association": "MEMBER",
        "head_ref": "hotfix/manual",
    }
    ok = await _orch()._ingest_pr_if_reviewable(
        svc, SimpleNamespace(id=uuid4()), pr, uuid4(), set()
    )
    assert ok is True
    assert svc.ingest_external_pr.await_args.kwargs["source"] == "internal_pr"


@pytest.mark.asyncio
async def test_skip_internal_lifecycle_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    # A live task owns the branch → it's the org's own integration PR → skip.
    monkeypatch.setattr(orch_mod.settings, "internal_pr_enabled", True)
    svc = _svc(owns_branch=True)
    pr = {
        "number": 9,
        "is_fork": False,
        "author_association": "MEMBER",
        "head_ref": "feature/main_pm/abc",
    }
    ok = await _orch()._ingest_pr_if_reviewable(
        svc, SimpleNamespace(id=uuid4()), pr, uuid4(), set()
    )
    assert ok is False
    svc.ingest_external_pr.assert_not_awaited()


@pytest.mark.asyncio
async def test_skip_internal_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orch_mod.settings, "internal_pr_enabled", False)
    svc = _svc()
    pr = {"number": 9, "is_fork": False, "author_association": "MEMBER"}
    ok = await _orch()._ingest_pr_if_reviewable(
        svc, SimpleNamespace(id=uuid4()), pr, uuid4(), set()
    )
    assert ok is False
    svc.ingest_external_pr.assert_not_awaited()


@pytest.mark.asyncio
async def test_skip_app_bot_authored_fleet_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    """The 2026-07-23 live incident: with a GitHub App bound, fleet PRs are
    authored by <app-slug>[bot] whose author_association is NONE — the external
    heuristic reads that as an outsider. Branch ownership must win: a same-repo
    PR whose head an active task owns is the org's own, whoever authored it."""
    monkeypatch.setattr(orch_mod.settings, "external_pr_enabled", True)
    monkeypatch.setattr(orch_mod.settings, "internal_pr_enabled", True)
    svc = _svc(owns_branch=True)
    pr = {
        "number": 667,
        "is_fork": False,
        "author_is_owner": False,
        "user_login": "roboco-app[bot]",
        "author_association": "NONE",
        "head_ref": "feature/frontend/170c9578--f1957610",
    }
    ok = await _orch()._ingest_pr_if_reviewable(
        svc, SimpleNamespace(id=uuid4()), pr, uuid4(), set()
    )
    assert ok is False
    svc.ingest_external_pr.assert_not_awaited()


@pytest.mark.asyncio
async def test_app_bot_pr_without_owning_task_still_reviews(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The documented residual: a same-repo bot-authored PR with NO active
    owning task falls through to the author heuristics and reviews as
    external — orphaned/unknown bot branches (dependabot included) keep
    getting the read-only adversarial review."""
    monkeypatch.setattr(orch_mod.settings, "external_pr_enabled", True)
    monkeypatch.setattr(orch_mod.settings, "internal_pr_enabled", True)
    svc = _svc(owns_branch=False)
    pr = {
        "number": 42,
        "is_fork": False,
        "author_is_owner": False,
        "user_login": "dependabot[bot]",
        "author_association": "NONE",
        "head_ref": "dependabot/npm_and_yarn/foo-1.2.3",
    }
    ok = await _orch()._ingest_pr_if_reviewable(
        svc, SimpleNamespace(id=uuid4()), pr, uuid4(), set()
    )
    assert ok is True
    assert svc.ingest_external_pr.await_args.kwargs["source"] == "external_pr"


@pytest.mark.asyncio
async def test_fork_pr_never_consults_branch_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fork PR is external by definition — the ownership pre-check must not
    run for it (a fork head ref can coincide with an org branch name)."""
    monkeypatch.setattr(orch_mod.settings, "external_pr_enabled", True)
    svc = _svc(owns_branch=True)
    pr = {
        "number": 7,
        "is_fork": True,
        "user_login": "outsider",
        "head_ref": "feature/frontend/copycat",
    }
    ok = await _orch()._ingest_pr_if_reviewable(
        svc, SimpleNamespace(id=uuid4()), pr, uuid4(), set()
    )
    assert ok is True
    svc.active_task_owns_branch.assert_not_awaited()
    assert svc.ingest_external_pr.await_args.kwargs["source"] == "external_pr"


@pytest.mark.asyncio
async def test_skip_owner_authored_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    # The org's own account opened the PR → self-review, never ingest (even with
    # both review modes on). The reviewer reviews PRs the org did NOT author.
    monkeypatch.setattr(orch_mod.settings, "external_pr_enabled", True)
    monkeypatch.setattr(orch_mod.settings, "internal_pr_enabled", True)
    svc = _svc()
    pr = {
        "number": 213,
        "is_fork": False,
        "author_is_owner": True,
        "author_association": "OWNER",
        "head_ref": "feat/grok-provider-seam",
    }
    ok = await _orch()._ingest_pr_if_reviewable(
        svc, SimpleNamespace(id=uuid4()), pr, uuid4(), set()
    )
    assert ok is False
    svc.ingest_external_pr.assert_not_awaited()
