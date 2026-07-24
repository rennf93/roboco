"""Board pitch doctrine + the Auditor dm nuance are actually in the prose.

Guards two prompt-drift classes: the `pitch` verb had no doctrine section
anywhere (no board agent ever had a reason to call it), and board.md /
auditor.md both flatly claimed the Auditor has NO `dm` when role_config.py
grants it a reply-only, CEO-thread-only `dm`/`read_a2a`.
"""

from __future__ import annotations

from roboco.agents.factories._base import _get_prompts_base_path, _load_layer

_PROMPTS = _get_prompts_base_path()


def test_board_role_prompt_documents_pitch() -> None:
    text = _load_layer(_PROMPTS / "roles" / "board.md")
    assert text, "board.md is missing or empty"
    assert "pitch(" in text
    assert "## Pitching a new product" in text


def test_product_owner_identity_lists_propose_roadmap_and_pitch() -> None:
    text = _load_layer(_PROMPTS / "identities" / "product-owner.md")
    assert text, "product-owner.md is missing or empty"
    assert "propose_roadmap" in text
    assert "pitch(" in text


def test_head_marketing_identity_lists_pitch() -> None:
    text = _load_layer(_PROMPTS / "identities" / "head-marketing.md")
    assert text, "head-marketing.md is missing or empty"
    assert "pitch(" in text


def test_board_role_prompt_no_longer_claims_flat_no_dm_for_auditor() -> None:
    text = _load_layer(_PROMPTS / "roles" / "board.md")
    assert "The Auditor is silent: read-only, no `dm`" not in text
    assert "You have no `dm`/`escalate_*`" not in text
    assert "reply in-thread when the CEO opens a DM with it" in text


def test_auditor_identity_no_longer_claims_flat_no_dm() -> None:
    text = _load_layer(_PROMPTS / "identities" / "auditor.md")
    assert text, "auditor.md is missing or empty"
    assert "You have **no** `dm` verb" not in text
    assert "only to read and reply in-thread when the CEO opens a DM" in text


def test_auditor_identity_documents_playbook_discovery_via_triage() -> None:
    text = _load_layer(_PROMPTS / "identities" / "auditor.md")
    assert "pending playbook draft" in text
    assert "triage()" in text
