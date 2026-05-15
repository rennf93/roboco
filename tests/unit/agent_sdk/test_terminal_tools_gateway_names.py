"""Smoke-7: SDK's _TERMINAL_TOOLS matches the current gateway verb names.

Original bug: the set still held pre-gateway names (roboco_agent_idle,
roboco_task_submit_qa, ...). Tools recorded via /terminal/tool_recorded
get their `mcp__roboco-flow__` prefix stripped, so `i_am_idle` lands in
recent_tools — but the membership check against the old names always
returned False. The stop hook then nagged every agent on every clean
exit, wasting tokens and burning the stop-allowance counter.
"""

from __future__ import annotations

from roboco.agent_sdk.server import _TERMINAL_TOOLS, _SessionState


def test_idle_is_terminal() -> None:
    """i_am_idle (the universal clean-exit verb) must be terminal."""
    assert "i_am_idle" in _TERMINAL_TOOLS


def test_developer_terminal_verbs() -> None:
    """A developer's clean-exit verbs must all be terminal."""
    for verb in ("i_am_done", "i_am_blocked", "unclaim"):
        assert verb in _TERMINAL_TOOLS, f"{verb} missing from _TERMINAL_TOOLS"


def test_qa_terminal_verbs() -> None:
    """QA's pass/fail verdicts and clean-exit verbs must all be terminal."""
    for verb in ("pass", "fail", "i_am_idle"):
        assert verb in _TERMINAL_TOOLS


def test_pm_terminal_verbs() -> None:
    """PM handoff verbs (complete, submit_up, escalate_*) must be terminal."""
    for verb in (
        "complete",
        "submit_up",
        "escalate_up",
        "escalate_to_ceo",
    ):
        assert verb in _TERMINAL_TOOLS


def test_documenter_terminal_verbs() -> None:
    """Documenter's i_documented + i_am_idle must be terminal."""
    for verb in ("i_documented", "i_am_idle"):
        assert verb in _TERMINAL_TOOLS


def test_pre_gateway_names_not_present() -> None:
    """Pre-gateway names must NOT be in the set (they never match recent_tools)."""
    pre_gateway = {
        "roboco_agent_idle",
        "roboco_task_submit_qa",
        "roboco_task_qa_pass",
        "roboco_task_qa_fail",
        "roboco_task_complete",
        "roboco_task_docs_complete",
        "roboco_task_escalate",
        "roboco_task_escalate_to_ceo",
    }
    leaked = pre_gateway & _TERMINAL_TOOLS
    assert not leaked, (
        f"Pre-gateway names leaked into _TERMINAL_TOOLS: {sorted(leaked)}. "
        "These never match the suffix-stripped MCP verb names recorded by "
        "/terminal/tool_recorded."
    )


def test_had_terminal_recently_after_i_am_idle() -> None:
    """Recording i_am_idle marks the session as having terminal recently."""
    state = _SessionState()
    # Mirrors the SDK's suffix-strip on /terminal/tool_recorded.
    state.recent_tools.append("i_am_idle")
    state.last_tool = "i_am_idle"
    assert state.had_terminal_recently() is True, (
        "Stop hook reads had_terminal_recently to decide if a Stop is graceful. "
        "After i_am_idle the agent's exit IS graceful — must return True."
    )
