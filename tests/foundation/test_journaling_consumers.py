"""Verify journaling-scope consumers derive from foundation."""

from __future__ import annotations

from roboco.enforcement import journal_perms
from roboco.enforcement.journal_perms import can_read_journal
from roboco.foundation.policy import journaling
from roboco.services.gateway import content_actions
from roboco.services.journal import _SCOPE_TO_TYPE


def test_content_actions_valid_scopes_match_foundation() -> None:
    foundation_values = {s.value for s in journaling.Scope}
    config_values = set(content_actions._VALID_NOTE_SCOPES)
    assert config_values == foundation_values, (
        f"_VALID_NOTE_SCOPES drift: {config_values ^ foundation_values}"
    )


def test_journal_service_scope_to_type_matches_foundation() -> None:
    # Service uses string keys; foundation uses Scope enum keys.
    foundation_str_keys = {s.value: t for s, t in journaling.SCOPE_TO_TYPE.items()}
    assert foundation_str_keys == _SCOPE_TO_TYPE, (
        f"_SCOPE_TO_TYPE drift: "
        f"foundation={foundation_str_keys}, service={_SCOPE_TO_TYPE}"
    )


def test_journal_perms_protected_journals_match_foundation() -> None:
    assert journal_perms.PROTECTED_JOURNALS == journaling.PROTECTED_JOURNALS


def test_journal_perms_validate_read_behavior_preserved() -> None:
    """Functional smoke: existing public surface still routes correctly.

    Pre-Phase-2 behavior the foundation tiers must reproduce:
    - CEO can read any journal (protected or not).
    - Auditor can read protected journals (e.g. CEO's).
    - Main PM can read any non-protected journal but NOT protected ones.
    - A developer can read their own journal and same-cell members'.
    - A developer cannot read another cell's journals.
    """
    # CEO reads anything (including protected auditor journal)
    can, _ = can_read_journal("ceo", "auditor")
    assert can is True
    # Auditor reads protected journals (CEO's)
    can, _ = can_read_journal("auditor", "ceo")
    assert can is True
    # Main PM reads non-protected
    can, _ = can_read_journal("main-pm", "be-dev-1")
    assert can is True
    # Main PM cannot read protected (CEO)
    can, _ = can_read_journal("main-pm", "ceo")
    assert can is False
    # Developer can read own journal
    can, _ = can_read_journal("be-dev-1", "be-dev-1")
    assert can is True
    # Developer can read same-cell QA
    can, _ = can_read_journal("be-dev-1", "be-qa")
    assert can is True
    # Developer cannot read other cell's journals
    can, _ = can_read_journal("be-dev-1", "fe-dev-1")
    assert can is False
