"""``AgentOrchestrator._description_body`` — the bounded description block for
the dev spawn prompt + SessionStart briefing. Pure static helper, no fixtures."""

from __future__ import annotations

from roboco.runtime.orchestrator import AgentOrchestrator

_PLACEHOLDER = "(none — ask the PM before proceeding)"


class TestDescriptionBody:
    def test_empty_returns_placeholder(self) -> None:
        assert AgentOrchestrator._description_body("") == _PLACEHOLDER
        assert AgentOrchestrator._description_body(None) == _PLACEHOLDER
        assert AgentOrchestrator._description_body("   \n  ") == _PLACEHOLDER

    def test_short_description_returned_verbatim(self) -> None:
        desc = "edit prompter.py:412 — thread task_id through update_live_batch"
        assert AgentOrchestrator._description_body(desc) == desc

    def test_long_description_capped_with_omitted_marker(self) -> None:
        cap = 4000
        desc = "x" * (cap + 1500)
        body = AgentOrchestrator._description_body(desc)
        assert body.startswith("x" * cap)
        assert "chars omitted" in body
        assert "evidence() carries" in body
        # The kept prefix is exactly the cap; the marker follows.
        assert len(body.split("\n… ", 1)[0]) == cap

    def test_custom_cap_respected(self) -> None:
        body = AgentOrchestrator._description_body("x" * 50, cap=10)
        assert body.startswith("x" * 10)
        assert "chars omitted" in body
