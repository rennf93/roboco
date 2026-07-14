"""Grok native --deny covers raw package-manager commands (Makefile guardrail).

Native ``--deny`` is graceful on grok (the model gets a permission error and
adapts to ``make``; the run continues — unlike a hook deny, which cancels the
whole run). So raw-PM commands are denied via ``_RAW_PM_DENY`` in
``_deny_rules()``, mirroring ``_GIT_MUTATE_DENY``. The bash-guard hook handles
only the compound-command case the globs miss, and there
``ROBOCO_GUARD_SKIP_PM=1`` nudges instead of canceling.
"""

from __future__ import annotations

from roboco.llm.providers.grok_cli_config import _RAW_PM_DENY, _deny_rules


def test_raw_pm_deny_rules_present() -> None:
    rules = _deny_rules("developer")
    assert "Bash(uv run*)" in rules
    assert "Bash(uv pip install*)" in rules
    assert "Bash(pip install*)" in rules
    assert "Bash(conda install*)" in rules
    assert "Bash(poetry run*)" in rules


def test_raw_pm_deny_is_subset_of_deny_rules() -> None:
    rules = set(_deny_rules("developer"))
    assert set(_RAW_PM_DENY) <= rules


def test_raw_pm_deny_only_for_bash_roles() -> None:
    """Non-bash roles have bash removed entirely — no deny rules at all."""
    assert _deny_rules("auditor") == []
