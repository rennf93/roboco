"""Generators must produce deterministic, predictable output."""

from __future__ import annotations

import json

from roboco.foundation import _generators


def test_render_intent_verbs_md_lists_every_intent() -> None:
    md = _generators.render_intent_verbs_md()
    assert "## i_will_work_on" in md
    assert "## open_pr" in md
    assert "## delegate" in md


def test_render_intent_verbs_md_includes_composes_for_each() -> None:
    md = _generators.render_intent_verbs_md()
    # i_will_work_on composes claim → set_plan → start; expect those names appear.
    assert "claim" in md
    assert "set_plan" in md
    assert "start" in md


def test_render_status_transitions_md_has_table_header() -> None:
    md = _generators.render_status_transitions_md()
    assert "| Source | Target | Action | Roles |" in md


def test_render_panel_json_emits_intents_array() -> None:
    payload = _generators.render_panel_json()
    parsed = json.loads(payload)
    assert "intents" in parsed
    assert isinstance(parsed["intents"], list)
    assert any(i["name"] == "i_will_work_on" for i in parsed["intents"])


def test_render_agent_prompt_fragment_for_developer_lists_dev_verbs() -> None:
    fragment = _generators.render_agent_prompt_fragment("developer")
    assert "i_will_work_on" in fragment
    assert "open_pr" in fragment
    assert "delegate" not in fragment  # PM only


def test_generators_are_deterministic() -> None:
    """Two consecutive renders produce the same bytes."""
    a = _generators.render_intent_verbs_md()
    b = _generators.render_intent_verbs_md()
    assert a == b
