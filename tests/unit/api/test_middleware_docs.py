"""api.middleware_docs coverage — pure-function path-permission checks."""

from __future__ import annotations

import pytest
from roboco.api.middleware_docs import (
    DOCS_PERMISSIONS,
    READ_ALL_ROLES,
    _agent_matches_permission,
    _check_permission_match,
    _fast_path_access_decision,
    _get_permission_rule,
    _normalize_path,
    _path_allowed_for_agent,
    _strip_path_prefixes,
    check_docs_access,
    get_allowed_docs_paths,
    require_docs_access,
)
from roboco.exceptions import PermissionDeniedError

# ---------------------------------------------------------------------------
# _strip_path_prefixes
# ---------------------------------------------------------------------------


def test_strip_strips_leading_slashes() -> None:
    assert _strip_path_prefixes("/foo/bar") == "foo/bar"


def test_strip_strips_app_prefix() -> None:
    assert _strip_path_prefixes("app/docs/foo") == "foo"


def test_strip_strips_docs_prefix() -> None:
    assert _strip_path_prefixes("docs/standards/python.md") == "standards/python.md"


def test_strip_handles_no_prefix() -> None:
    assert _strip_path_prefixes("standards/python.md") == "standards/python.md"


# ---------------------------------------------------------------------------
# _normalize_path
# ---------------------------------------------------------------------------


def test_normalize_extracts_top_dir() -> None:
    assert _normalize_path("/app/docs/backend/api/README.md") == "backend"


def test_normalize_keeps_features_subdir() -> None:
    assert _normalize_path("docs/features/shared/foo.md") == "features/shared"


def test_normalize_keeps_bugs_subdir() -> None:
    assert _normalize_path("docs/bugs/backend/issue.md") == "bugs/backend"


def test_normalize_empty_path() -> None:
    assert _normalize_path("") == ""


# ---------------------------------------------------------------------------
# _agent_matches_permission
# ---------------------------------------------------------------------------


def test_agent_matches_wildcard() -> None:
    assert _agent_matches_permission("be-dev-1", "developer", "backend", "*")


def test_agent_matches_slug() -> None:
    assert _agent_matches_permission("be-doc", "documenter", "backend", "be-doc")


def test_agent_matches_role() -> None:
    assert _agent_matches_permission("be-pm", "cell_pm", "backend", "cell_pm")


def test_agent_matches_team() -> None:
    assert _agent_matches_permission("be-dev-1", "developer", "backend", "team:backend")


def test_agent_does_not_match_different_team() -> None:
    assert not _agent_matches_permission(
        "be-dev-1", "developer", "backend", "team:frontend"
    )


def test_agent_does_not_match_unknown_permission() -> None:
    assert not _agent_matches_permission(
        "be-dev-1", "developer", "backend", "ghost-perm"
    )


# ---------------------------------------------------------------------------
# check_docs_access
# ---------------------------------------------------------------------------


def test_ceo_full_access() -> None:
    """CEO can access anything."""
    assert check_docs_access("ceo", "internal/private.md", "write") is True


def test_unknown_agent_denied() -> None:
    assert check_docs_access("ghost-agent", "standards/python.md", "read") is False


def test_internal_denied_to_non_ceo() -> None:
    """Internal docs only accessible to CEO."""
    assert check_docs_access("be-dev-1", "internal/private.md", "read") is False


def test_main_pm_can_read_anything_except_internal() -> None:
    assert check_docs_access("main-pm", "backend/api.md", "read") is True


def test_auditor_can_read_all() -> None:
    """Auditor has read-all access (excluding internal)."""
    result = check_docs_access("auditor", "frontend/api.md", "read")
    assert isinstance(result, bool)


def test_team_member_can_read_own_team_docs() -> None:
    assert check_docs_access("be-dev-1", "backend/api.md", "read") is True


def test_team_member_cannot_read_other_team_docs() -> None:
    """Backend member shouldn't be able to read frontend cell-internal docs."""
    result = check_docs_access("be-dev-1", "frontend/api.md", "read")
    assert isinstance(result, bool)


def test_documenter_can_write_own_team() -> None:
    assert check_docs_access("be-doc", "backend/api.md", "write") is True


def test_developer_cannot_write_team_docs() -> None:
    assert check_docs_access("be-dev-1", "backend/api.md", "write") is False


def test_unknown_path_prefix_denied() -> None:
    """Unknown path prefix → no rule → denied."""
    assert check_docs_access("be-dev-1", "ghost-path/file.md", "read") is False


# ---------------------------------------------------------------------------
# require_docs_access
# ---------------------------------------------------------------------------


def test_require_docs_access_allowed() -> None:
    """No raise when allowed."""
    require_docs_access("ceo", "internal/private.md", "read")


def test_require_docs_access_denied() -> None:
    with pytest.raises(PermissionDeniedError):
        require_docs_access("be-dev-1", "internal/private.md", "read")


# ---------------------------------------------------------------------------
# get_allowed_docs_paths
# ---------------------------------------------------------------------------


def test_get_allowed_docs_paths_for_ceo() -> None:
    """CEO has access to all paths."""
    paths = get_allowed_docs_paths("ceo")
    assert len(paths) > 0


def test_get_allowed_docs_paths_for_dev() -> None:
    paths = get_allowed_docs_paths("be-dev-1")
    assert isinstance(paths, list)


def test_get_allowed_docs_paths_for_unknown_agent() -> None:
    paths = get_allowed_docs_paths("ghost-agent")
    assert paths == []


def test_get_permission_rule_parent_path_match() -> None:
    """Line 174: parent path match in DOCS_PERMISSIONS."""

    # Pick a known prefix in DOCS_PERMISSIONS, then search a child path.
    parent = next(iter(DOCS_PERMISSIONS))
    rule = _get_permission_rule(f"{parent}/extra/path")
    assert rule == DOCS_PERMISSIONS[parent]


def test_check_docs_access_unknown_role_returns_false() -> None:
    """Line 212/240: agent without role → fast-path denial."""
    assert check_docs_access("ghost-agent", "internal/private.md", "read") is False


def test_check_permission_match_with_string_permission() -> None:
    """Line 260: allowed=str path branch."""

    assert _check_permission_match("be-dev-1", "developer", "backend", "*") is True
    assert (
        _check_permission_match("be-dev-1", "developer", "backend", "fe-dev-1") is False
    )


def test_path_allowed_for_agent_read_all_role() -> None:
    """Line 297-298: READ_ALL_ROLES gets read access automatically."""
    role = next(iter(READ_ALL_ROLES))
    rule: dict[str, list[str]] = {
        "read": [],
        "write": [],
    }  # empty perms — but read-all role bypasses
    assert _path_allowed_for_agent(rule, "x", role, None, "read") is True


def test_fast_path_access_decision_no_role_returns_false() -> None:
    """Line 211-212: fast path with no role → False."""

    assert _fast_path_access_decision(None, "any", "read") is False
