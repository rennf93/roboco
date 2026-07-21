"""enforcement.a2a_access coverage."""

from __future__ import annotations

import pytest
from roboco.agents_config import can_a2a_direct
from roboco.enforcement.a2a_access import (
    A2AAccessDeniedError,
    get_a2a_allowed_targets,
    validate_a2a_access,
)
from roboco.foundation.policy.communications import NO_COMMS_ROLES


def test_validate_a2a_self_a2a_denied() -> None:
    with pytest.raises(A2AAccessDeniedError, match="cannot A2A yourself"):
        validate_a2a_access("be-dev-1", "be-dev-1")


def test_validate_a2a_to_ceo_denied() -> None:
    with pytest.raises(A2AAccessDeniedError):
        validate_a2a_access("be-dev-1", "ceo")


def test_validate_a2a_within_cell() -> None:
    """Cell members can A2A within their cell."""
    result = validate_a2a_access("be-dev-1", "be-qa")
    assert result is True


def test_a2a_access_denied_error_has_attributes() -> None:
    err = A2AAccessDeniedError(
        from_agent="be-dev-1",
        to_agent="ceo",
        reason="CEO is human",
    )
    assert err.from_agent == "be-dev-1"
    assert err.to_agent == "ceo"


def test_get_a2a_allowed_targets_returns_list() -> None:
    targets = get_a2a_allowed_targets("be-dev-1", ["be-qa", "be-pm", "fe-dev-1", "ceo"])
    assert isinstance(targets, list)


def test_get_a2a_allowed_targets_excludes_ceo() -> None:
    targets = get_a2a_allowed_targets("be-dev-1", ["ceo"])
    assert "ceo" not in targets


def test_get_a2a_allowed_targets_excludes_self() -> None:
    targets = get_a2a_allowed_targets("be-dev-1", ["be-dev-1", "be-qa"])
    # Self should be filtered.
    assert "be-dev-1" not in targets or "be-qa" in targets


# ---------------------------------------------------------------------------
# CEO-initiated A2A — the one asymmetric rule: CEO may send, nobody may
# target CEO (the block above must still hold).
# ---------------------------------------------------------------------------


def test_validate_a2a_access_ceo_to_agent_allowed() -> None:
    result = validate_a2a_access("ceo", "be-dev-1")
    assert result is True


def test_can_a2a_direct_ceo_to_main_pm() -> None:
    assert can_a2a_direct("ceo", "main-pm") == (True, None)


def test_can_a2a_direct_ceo_to_board_member() -> None:
    """Board members are normally unreachable via direct A2A for everyone
    else (routed through main-pm) — CEO is exempt from that restriction."""
    assert can_a2a_direct("ceo", "product-owner") == (True, None)


def test_validate_a2a_to_ceo_still_denied_with_ceo_send_rule() -> None:
    """Regression: allowing CEO-initiated A2A must not loosen the inbound
    block — nobody may target the CEO."""
    with pytest.raises(A2AAccessDeniedError):
        validate_a2a_access("be-dev-1", "ceo")


def test_get_a2a_allowed_targets_ceo_includes_all_roles() -> None:
    targets = get_a2a_allowed_targets("ceo", ["be-dev-1", "be-qa", "main-pm"])
    assert set(targets) == {"be-dev-1", "be-qa", "main-pm"}


def test_can_a2a_direct_to_ceo_message_explains_reply_only() -> None:
    """An agent can never INITIATE with the CEO (only reply inside a
    conversation the CEO opened) — the matrix denial message must say so,
    not point at the old blanket 'use notify()' framing."""
    allowed, reason = can_a2a_direct("be-dev-1", "ceo")
    assert allowed is False
    assert reason is not None
    assert "reply" in reason.lower()


@pytest.mark.parametrize(
    "target_slug",
    ["intake-1", "secretary-1"],
)
def test_can_a2a_direct_ceo_to_no_comms_role_denied(target_slug: str) -> None:
    """The CEO's asymmetric reach still can't target a role with no dm/
    read_a2a on its manifest (prompter, secretary — human-only, own chat
    pages) — nothing on the other end could ever read or answer the DM."""
    allowed, reason = can_a2a_direct("ceo", target_slug)
    assert allowed is False
    assert reason is not None
    assert "comms" in reason.lower()


@pytest.mark.parametrize("target_slug", ["auditor", "pr-reviewer-1"])
def test_can_a2a_direct_ceo_to_auditor_or_pr_reviewer_allowed(target_slug: str) -> None:
    """The CEO can now DM a mid-flight auditor or PR reviewer — both carry
    dm/read_a2a so they can read and reply in-thread, even though neither
    gains a peer-initiation surface (auditor stays silent via
    can_a2a_direct; the PR reviewer stays scoped to its owning PM)."""
    allowed, reason = can_a2a_direct("ceo", target_slug)
    assert allowed is True
    assert reason is None


def test_can_a2a_direct_ceo_to_no_comms_role_reuses_canonical_set() -> None:
    """The refusal set must be exactly foundation.policy.communications'
    NO_COMMS_ROLES — the same set services.gateway.content_actions uses to
    gate the dm() sender side — so the two never drift apart."""
    expected = {"prompter", "secretary"}
    assert {role.value for role in NO_COMMS_ROLES} == expected
