"""agents_config coverage — pure-function role/team resolution."""

from __future__ import annotations

import json as _json
from base64 import urlsafe_b64decode
from collections import Counter
from typing import TYPE_CHECKING, cast

from roboco.agents_config import (
    A2A_ALLOWED_PAIRS,
    can_a2a_direct,
    can_assign_tasks,
    can_cancel_tasks,
    can_create_tasks,
    can_send_notifications,
    get_a2a_route_hint,
    get_agent_cell,
    get_agent_role,
    get_agent_skills,
    get_agent_team,
    get_cell_members,
    get_escalation_target,
    get_pm_for_agent,
    get_pm_for_team,
    is_board_member,
    is_ceo,
    is_management,
    is_pm,
    issue_agent_token,
    issue_panel_token,
    verify_agent_token,
)
from roboco.foundation import identity as foundation
from roboco.seeds.initial_data import CEO_AGENT_ID

if TYPE_CHECKING:
    import pytest

# ---------------------------------------------------------------------------
# get_agent_role / get_agent_team / get_agent_cell
# ---------------------------------------------------------------------------


def test_get_agent_role_known() -> None:
    assert get_agent_role("be-dev-1") == "developer"


def test_get_agent_role_main_pm() -> None:
    assert get_agent_role("main-pm") == "main_pm"


def test_get_agent_role_unknown() -> None:
    assert get_agent_role("ghost-agent") == "unknown"


def test_get_agent_team_known() -> None:
    assert get_agent_team("be-dev-1") == "backend"


def test_get_agent_team_for_main_pm_returns_value() -> None:
    """main-pm has team 'main_pm' or None depending on config."""
    result = get_agent_team("main-pm")
    assert result is None or isinstance(result, str)


def test_get_agent_team_for_unknown() -> None:
    assert get_agent_team("ghost-agent") is None


def test_get_agent_cell_alias_for_team() -> None:
    assert get_agent_cell("be-dev-1") == "backend"


# ---------------------------------------------------------------------------
# Role predicates
# ---------------------------------------------------------------------------


def test_is_pm_for_cell_pm() -> None:
    assert is_pm("be-pm") is True


def test_is_pm_for_main_pm() -> None:
    assert is_pm("main-pm") is True


def test_is_pm_for_developer() -> None:
    assert is_pm("be-dev-1") is False


def test_is_management_for_main_pm() -> None:
    assert is_management("main-pm") is True


def test_is_management_for_developer() -> None:
    assert is_management("be-dev-1") is False


def test_is_ceo_for_ceo() -> None:
    assert is_ceo("ceo") is True


def test_is_ceo_for_developer() -> None:
    assert is_ceo("be-dev-1") is False


def test_is_board_member() -> None:
    # Whatever the actual board members are, the function should return bool.
    assert isinstance(is_board_member("auditor"), bool)


# ---------------------------------------------------------------------------
# Permission predicates
# ---------------------------------------------------------------------------


def test_can_send_notifications_main_pm() -> None:
    assert can_send_notifications("main-pm") is True


def test_can_send_notifications_developer() -> None:
    assert can_send_notifications("be-dev-1") is False


def test_can_create_tasks_main_pm() -> None:
    assert can_create_tasks("main-pm") is True


def test_can_create_tasks_developer() -> None:
    assert can_create_tasks("be-dev-1") is False


def test_can_assign_tasks_main_pm() -> None:
    assert can_assign_tasks("main-pm") is True


def test_can_assign_tasks_developer() -> None:
    assert can_assign_tasks("be-dev-1") is False


def test_can_cancel_tasks_pm() -> None:
    assert can_cancel_tasks("main-pm") is True


def test_can_cancel_tasks_ceo() -> None:
    """CEO cannot cancel — they observe only."""
    assert can_cancel_tasks("ceo") is False


def test_can_cancel_tasks_auditor() -> None:
    assert can_cancel_tasks("auditor") is False


# ---------------------------------------------------------------------------
# Escalation + PM resolution
# ---------------------------------------------------------------------------


def test_get_escalation_target() -> None:
    target = get_escalation_target("be-dev-1")
    assert target is None or isinstance(target, str)


def test_get_pm_for_team_known() -> None:
    assert get_pm_for_team("backend") == "be-pm"


def test_get_pm_for_team_unknown() -> None:
    assert get_pm_for_team("mars") is None


def test_get_pm_for_agent_cell_pm_returns_main_pm() -> None:
    assert get_pm_for_agent("be-pm") == "main-pm"


def test_get_pm_for_agent_developer() -> None:
    pm = get_pm_for_agent("be-dev-1")
    # Cell members' PM is their cell PM.
    assert pm == "be-pm" or pm is None


# ---------------------------------------------------------------------------
# A2A policy
# ---------------------------------------------------------------------------


def test_can_a2a_direct_to_ceo_denied() -> None:
    allowed, reason = can_a2a_direct("be-dev-1", "ceo")
    assert allowed is False
    assert reason is not None


def test_can_a2a_direct_within_cell() -> None:
    allowed, _ = can_a2a_direct("be-dev-1", "be-qa")
    assert isinstance(allowed, bool)


def test_get_a2a_route_hint_returns_string() -> None:
    hint = get_a2a_route_hint("be-dev-1", "fe-dev-1")
    assert isinstance(hint, str)


def test_get_a2a_route_hint_for_ceo() -> None:
    hint = get_a2a_route_hint("be-dev-1", "ceo")
    assert "CEO" in hint


# ---------------------------------------------------------------------------
# get_cell_members
# ---------------------------------------------------------------------------


def test_get_cell_members_backend() -> None:
    members = get_cell_members("backend")
    assert isinstance(members, list)


def test_get_cell_members_unknown() -> None:
    assert get_cell_members("mars") == []


# ---------------------------------------------------------------------------
# get_agent_skills
# ---------------------------------------------------------------------------


def test_get_agent_skills_returns_list() -> None:
    skills = get_agent_skills("be-dev-1")
    assert isinstance(skills, list)


def test_get_agent_skills_unknown_agent() -> None:
    skills = get_agent_skills("ghost-agent")
    assert isinstance(skills, list)


# ---------------------------------------------------------------------------
# Token issuance + verification (lines 45-46, 67-72, 83-89)
# ---------------------------------------------------------------------------


def test_issue_agent_token_returns_unsigned_when_secret_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ROBOCO_AGENT_AUTH_SECRET", raising=False)
    assert issue_agent_token("be-dev-1", "developer", "backend") == "UNSIGNED"


_SHA256_HEX_LEN = 64


def test_issue_agent_token_signs_when_secret_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", "test-secret")
    tok = issue_agent_token("be-dev-1", "developer", "backend")
    assert tok != "UNSIGNED"
    # 64 hex chars from sha256
    assert len(tok) == _SHA256_HEX_LEN


def test_verify_agent_token_round_trips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", "rt-secret")
    tok = issue_agent_token("be-dev-1", "developer", "backend")
    assert verify_agent_token(tok, "be-dev-1", "developer", "backend") is True


def test_verify_agent_token_rejects_when_secret_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ROBOCO_AGENT_AUTH_SECRET", raising=False)
    assert verify_agent_token("anything", "be-dev-1", "developer", "backend") is False


def test_verify_agent_token_rejects_unsigned_sentinel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", "any-secret")
    assert verify_agent_token("UNSIGNED", "be-dev-1", "developer", "backend") is False


def test_verify_agent_token_rejects_empty_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", "any-secret")
    assert verify_agent_token("", "be-dev-1", "developer", "backend") is False


def test_verify_agent_token_rejects_mismatched_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", "real-secret")
    tok = issue_agent_token("be-dev-1", "developer", "backend")
    # Verify with different role → mismatch.
    assert verify_agent_token(tok, "be-dev-1", "qa", "backend") is False


# ---------------------------------------------------------------------------
# issue_panel_token — the panel's CEO credential for secure mode
# ---------------------------------------------------------------------------


def test_issue_panel_token_verifies_under_panel_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The panel token must verify under the EXACT headers the panel sends:
    X-Agent-Id = CEO uuid, X-Agent-Role = ceo, and NO team (empty)."""
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", "panel-secret")
    tok = issue_panel_token()
    assert verify_agent_token(tok, CEO_AGENT_ID, "ceo", "") is True


def test_issue_panel_token_unsigned_without_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ROBOCO_AGENT_AUTH_SECRET", raising=False)
    assert issue_panel_token() == "UNSIGNED"


def test_panel_token_does_not_grant_other_roles_or_identities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The panel token is bound to the CEO identity — it cannot be replayed to
    claim a different role or agent id."""
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", "panel-secret")
    tok = issue_panel_token()
    assert verify_agent_token(tok, CEO_AGENT_ID, "developer", "") is False
    assert verify_agent_token(tok, "be-dev-1", "ceo", "") is False


# ---------------------------------------------------------------------------
# Expiring token format (M35) — ttl_seconds ⇒ {payload}.{sig} with iat/exp
# ---------------------------------------------------------------------------


def _payload_of(token: str) -> dict[str, object]:
    pb = token.split(".", 1)[0]
    pad = "=" * (-len(pb) % 4)
    return cast("dict[str, object]", _json.loads(urlsafe_b64decode(pb + pad)))


def test_issue_agent_token_with_ttl_uses_expiring_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", "exp-secret")
    tok = issue_agent_token("be-dev-1", "developer", "backend", ttl_seconds=3600)
    assert "." in tok
    payload = _payload_of(tok)
    assert payload["id"] == "be-dev-1"
    assert payload["role"] == "developer"
    assert payload["team"] == "backend"
    assert isinstance(payload["iat"], (int, float))
    assert payload["exp"] == payload["iat"] + 3600


def test_verify_accepts_unexpired_ttl_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", "exp-secret")
    tok = issue_agent_token(
        "be-dev-1", "developer", "backend", ttl_seconds=3600, now=1_000_000.0
    )
    assert (
        verify_agent_token(tok, "be-dev-1", "developer", "backend", now=1_000_100.0)
        is True
    )


def test_verify_rejects_expired_ttl_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", "exp-secret")
    tok = issue_agent_token(
        "be-dev-1", "developer", "backend", ttl_seconds=3600, now=1_000_000.0
    )
    # 1 second past exp → rejected.
    assert (
        verify_agent_token(tok, "be-dev-1", "developer", "backend", now=1_003_601.0)
        is False
    )


def test_verify_ttl_token_rejects_tampered_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", "exp-secret")
    tok = issue_agent_token("be-dev-1", "developer", "backend", ttl_seconds=3600)
    pb, sig = tok.split(".", 1)
    # Flip the first payload char's case — JSON base64url payloads start with
    # "eyJ" (`{` → eyJ), so pb[0] is always alpha and the swap stays in-alphabet
    # while changing the bytes the HMAC covers.
    tampered = pb[0].swapcase() + pb[1:]
    assert (
        verify_agent_token(f"{tampered}.{sig}", "be-dev-1", "developer", "backend")
        is False
    )


def test_verify_ttl_token_rejects_wrong_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", "exp-secret")
    tok = issue_agent_token("be-dev-1", "developer", "backend", ttl_seconds=3600)
    assert verify_agent_token(tok, "be-dev-1", "qa", "backend") is False


def test_verify_ttl_token_rejects_wrong_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", "secret-a")
    tok = issue_agent_token("be-dev-1", "developer", "backend", ttl_seconds=3600)
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", "secret-b")
    assert verify_agent_token(tok, "be-dev-1", "developer", "backend") is False


def test_issue_agent_token_without_ttl_keeps_static_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backward compat: no ttl_seconds ⇒ old 64-hex static digest (panel path)."""
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", "static-secret")
    tok = issue_agent_token("be-dev-1", "developer", "backend")
    assert "." not in tok
    assert len(tok) == _SHA256_HEX_LEN


# ---------------------------------------------------------------------------
# get_pm_for_agent main_pm escalation (line 360)
# ---------------------------------------------------------------------------


def test_get_pm_for_agent_main_pm_returns_product_owner() -> None:
    assert get_pm_for_agent("main-pm") == "product-owner"


# ---------------------------------------------------------------------------
# A2A check branches: cell PM rejects board (line 682), cell-member without
# team falls through to management route (line 704), board successful path
# (732), main_pm dispatch (744), cell-member outbound to no-team (771-772).
# ---------------------------------------------------------------------------


def test_can_a2a_direct_cell_pm_to_board_denied() -> None:
    allowed, reason = can_a2a_direct("be-pm", "product-owner")
    assert allowed is False
    assert reason is not None
    assert "main-pm" in reason


def test_can_a2a_direct_cell_member_to_unknown_management() -> None:
    """Cell dev → CEO routes through CEO branch, not management."""
    # Dev → main-pm: management → falls into cell_member branch's else case.
    allowed, reason = can_a2a_direct("be-dev-1", "main-pm")
    # Either path works; just ensure structured output.
    assert isinstance(allowed, bool)
    assert reason is None or isinstance(reason, str)


def test_can_a2a_direct_main_pm_to_developer_denied() -> None:
    allowed, reason = can_a2a_direct("main-pm", "be-dev-1")
    # main-pm to a developer routed via cell PM.
    assert allowed is False
    assert reason is not None


def test_can_a2a_direct_main_pm_to_cell_pm_allowed() -> None:
    allowed, _ = can_a2a_direct("main-pm", "be-pm")
    assert allowed is True


def test_can_a2a_direct_board_to_main_pm_allowed() -> None:
    allowed, _ = can_a2a_direct("product-owner", "main-pm")
    assert allowed is True


def test_can_a2a_direct_board_to_developer_denied() -> None:
    allowed, reason = can_a2a_direct("product-owner", "be-dev-1")
    assert allowed is False
    assert reason is not None


def test_can_a2a_direct_pr_reviewer_to_main_pm_allowed() -> None:
    """The root→master gate reviewer delivers pr_fail change-requests to the
    owning Main PM. Denying it silently strands the verdict (blind re-submit)."""
    allowed, reason = can_a2a_direct("pr-reviewer-1", "main-pm")
    assert allowed is True
    assert reason is None


def test_can_a2a_direct_cell_pr_reviewer_to_cell_pm_allowed() -> None:
    """The cell→root gate reviewer delivers its verdict to the owning cell PM."""
    allowed, reason = can_a2a_direct("be-pr-reviewer", "be-pm")
    assert allowed is True
    assert reason is None


def test_can_a2a_direct_pr_reviewer_to_developer_denied() -> None:
    """A PR reviewer only A2As the owning PM — never devs/qa directly."""
    allowed, reason = can_a2a_direct("pr-reviewer-1", "be-dev-1")
    assert allowed is False
    assert reason is not None


def test_get_a2a_route_hint_cell_member_to_management() -> None:
    """Non-CEO cell-member → management agent (no team) routes via cell PM."""
    hint = get_a2a_route_hint("be-dev-1", "main-pm")
    # main-pm has team None → falls through to management branch (771-772).
    assert "be-pm" in hint or "main-pm" in hint or "Use" in hint


def test_can_a2a_direct_cell_member_to_unknown_returns_management_hint() -> None:
    """Cell-member → agent with unresolvable team hits management branch (704)."""
    allowed, reason = can_a2a_direct("be-dev-1", "ghost-agent")
    assert allowed is False
    assert reason is not None
    assert "be-pm" in reason or "main-pm" in reason


def test_can_a2a_direct_unknown_from_agent_falls_through() -> None:
    """An agent with no team hits the final fallback (line 750)."""
    allowed, reason = can_a2a_direct("ghost", "be-dev-1")
    assert allowed is False
    assert reason is not None
    assert "not permitted" in reason


def test_get_a2a_route_hint_cell_member_to_unknown_routes_via_cell_pm() -> None:
    """Cell-member → unknown agent (no team) hits lines 770-772."""
    hint = get_a2a_route_hint("be-dev-1", "ghost-agent")
    assert "be-pm" in hint
    assert "main-pm" in hint


def test_get_a2a_route_hint_unknown_from_agent_falls_through() -> None:
    """from_agent with no team falls through to escalate fallback (line 774)."""
    hint = get_a2a_route_hint("ghost", "be-dev-1")
    assert "escalate" in hint.lower()


# ---------------------------------------------------------------------------
# A2A_ALLOWED_PAIRS — the switchboard's static org-chart pair matrix
# ---------------------------------------------------------------------------

_EXPECTED_PAIR_COUNT = 88
_EXPECTED_GROUP_COUNTS = {
    "board": 3,
    "ceo": 18,
    "cell-backend": 15,
    "cell-frontend": 15,
    "cell-ux_ui": 15,
    "cross": 16,
    "pm-chain": 6,
}


def test_a2a_allowed_pairs_total_count() -> None:
    assert len(A2A_ALLOWED_PAIRS) == _EXPECTED_PAIR_COUNT


def test_a2a_allowed_pairs_canonical_lexical_order() -> None:
    """agent_a < agent_b always — matches A2AConversationTable's canonical
    ordering, so the service's DB join keys line up."""
    for p in A2A_ALLOWED_PAIRS:
        assert p.agent_a < p.agent_b


def test_a2a_allowed_pairs_no_duplicates() -> None:
    keys = [(p.agent_a, p.agent_b) for p in A2A_ALLOWED_PAIRS]
    assert len(keys) == len(set(keys))


def test_a2a_allowed_pairs_excludes_non_participants_keeps_ceo() -> None:
    """The intake interviewer, the secretary, and the system sentinel are not
    A2A participants — but the CEO is (asymmetric panel DMs), so its pairs
    must be in the matrix."""
    slugs = {p.agent_a for p in A2A_ALLOWED_PAIRS} | {
        p.agent_b for p in A2A_ALLOWED_PAIRS
    }
    for excluded in ("intake-1", "secretary-1", "system"):
        assert excluded not in slugs
    assert "ceo" in slugs


def test_a2a_allowed_pairs_ceo_paired_with_every_dm_capable_agent() -> None:
    """CEO → anyone with an agent-comms surface is allowed, so every non-CEO
    switchboard slug EXCEPT the no-comms roles (auditor, pr_reviewer — no
    dm/read_a2a on their manifest, so a CEO DM to them is a black hole)
    appears in exactly one ``ceo``-group pair."""
    ceo_pairs = [p for p in A2A_ALLOWED_PAIRS if "ceo" in (p.agent_a, p.agent_b)]
    non_ceo_slugs = (
        {p.agent_a for p in A2A_ALLOWED_PAIRS} | {p.agent_b for p in A2A_ALLOWED_PAIRS}
    ) - {"ceo"}
    dm_capable_slugs = {
        s for s in non_ceo_slugs if get_agent_role(s) not in ("auditor", "pr_reviewer")
    }
    assert all(p.group_key == "ceo" for p in ceo_pairs)
    assert len(ceo_pairs) == len(dm_capable_slugs)
    # And the no-comms roles are confirmed absent from any ceo-group pair.
    ceo_slugs = {p.agent_a for p in ceo_pairs} | {p.agent_b for p in ceo_pairs}
    assert ceo_slugs.isdisjoint(non_ceo_slugs - dm_capable_slugs)


def test_a2a_allowed_pairs_group_key_counts() -> None:
    counts = Counter(p.group_key for p in A2A_ALLOWED_PAIRS)
    assert dict(counts) == _EXPECTED_GROUP_COUNTS


def test_a2a_allowed_pairs_contains_same_cell_pair() -> None:
    assert any(
        {p.agent_a, p.agent_b} == {"be-dev-1", "be-qa"} for p in A2A_ALLOWED_PAIRS
    )


def test_a2a_allowed_pairs_contains_pm_chain_pair() -> None:
    assert any(
        {p.agent_a, p.agent_b} == {"be-pm", "main-pm"} for p in A2A_ALLOWED_PAIRS
    )


def test_a2a_allowed_pairs_contains_board_pair() -> None:
    assert any(
        {p.agent_a, p.agent_b} == {"auditor", "product-owner"}
        for p in A2A_ALLOWED_PAIRS
    )


def test_a2a_allowed_pairs_reflects_can_a2a_direct_matrix() -> None:
    """Every listed pair allows >=1 direction per the live matrix — catches
    drift if can_a2a_direct changes without regenerating the static list."""
    for p in A2A_ALLOWED_PAIRS:
        allowed_ab, _ = can_a2a_direct(p.agent_a, p.agent_b)
        allowed_ba, _ = can_a2a_direct(p.agent_b, p.agent_a)
        assert allowed_ab or allowed_ba


def test_a2a_allowed_pairs_role_team_fields_match_registry() -> None:
    for p in A2A_ALLOWED_PAIRS:
        assert p.role_a == foundation.AGENTS[p.agent_a].role.value
        assert p.team_a == foundation.AGENTS[p.agent_a].team.value
        assert p.role_b == foundation.AGENTS[p.agent_b].role.value
        assert p.team_b == foundation.AGENTS[p.agent_b].team.value
