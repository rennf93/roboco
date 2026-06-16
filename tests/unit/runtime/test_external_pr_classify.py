"""External-PR author/fork classification — the inbound trust decision.

``_is_external_pr`` decides whether an open PR was authored by the org itself
or by an outside contributor. It must default to *external* (the cautious
side) for anything it does not positively recognize as internal.
"""

from __future__ import annotations

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator


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
    ],
)
def test_parse_supersede_pr(quick_context: str, expected: int | None) -> None:
    assert AgentOrchestrator._parse_supersede_pr(quick_context) == expected
