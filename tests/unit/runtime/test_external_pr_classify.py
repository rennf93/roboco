"""External-PR author/fork classification — the inbound trust decision.

``_is_external_pr`` decides whether an open PR was authored by the org itself
or by an outside contributor. It must default to *external* (the cautious
side) for anything it does not positively recognize as internal.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
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
