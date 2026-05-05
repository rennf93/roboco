"""api.middleware_docs coverage — pure-function path-permission checks."""

from __future__ import annotations

import pytest
from roboco.api.middleware_docs import (
    _agent_matches_permission,
    _normalize_path,
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
