"""enforcement.journal_perms coverage."""

from __future__ import annotations

import pytest
from roboco.enforcement.journal_perms import (
    JournalAccessDeniedError,
    can_read_journal,
    get_readable_journals,
    validate_journal_access,
)


def test_self_can_read_own_journal() -> None:
    can, _ = can_read_journal("be-dev-1", "be-dev-1")
    assert can is True


def test_protected_ceo_journal_only_ceo_or_auditor() -> None:
    can, _ = can_read_journal("be-dev-1", "ceo")
    assert can is False


def test_ceo_can_read_any_journal() -> None:
    can, _ = can_read_journal("ceo", "be-dev-1")
    assert can is True


def test_auditor_can_read_any_non_protected() -> None:
    can, _ = can_read_journal("auditor", "be-dev-1")
    assert can is True


def test_main_pm_can_read_any_non_protected() -> None:
    can, _ = can_read_journal("main-pm", "be-dev-1")
    assert can is True


def test_cell_pm_can_read_own_cell() -> None:
    can, _ = can_read_journal("be-pm", "be-dev-1")
    assert can is True


def test_cell_pm_cannot_read_other_cell_dev() -> None:
    can, _ = can_read_journal("be-pm", "fe-dev-1")
    # Cross-cell access for cell PM is False unless target is also a PM.
    assert can is False


def test_cell_pm_can_read_other_cell_pm() -> None:
    can, _ = can_read_journal("be-pm", "fe-pm")
    assert can is True


def test_cell_member_same_cell() -> None:
    can, _ = can_read_journal("be-dev-1", "be-qa")
    assert can is True


def test_cell_member_cross_cell_denied() -> None:
    can, _ = can_read_journal("be-dev-1", "fe-dev-1")
    assert can is False


def test_validate_journal_access_raises_on_denied() -> None:
    with pytest.raises(JournalAccessDeniedError):
        validate_journal_access("be-dev-1", "fe-dev-1")


def test_validate_journal_access_passes() -> None:
    assert validate_journal_access("be-dev-1", "be-qa") is True


def test_get_readable_journals_for_ceo() -> None:
    info = get_readable_journals("ceo")
    assert info["scope"] == "all"


def test_get_readable_journals_for_main_pm() -> None:
    info = get_readable_journals("main-pm")
    assert info["scope"] == "all_cells"


def test_get_readable_journals_for_cell_pm() -> None:
    info = get_readable_journals("be-pm")
    assert info["scope"] == "cell_plus_pms"


def test_get_readable_journals_for_developer() -> None:
    info = get_readable_journals("be-dev-1")
    assert info["scope"] == "cell"


def test_get_readable_journals_for_unknown() -> None:
    info = get_readable_journals("ghost-agent")
    assert info["scope"] == "none"
