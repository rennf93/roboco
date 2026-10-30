"""Digest-presence tests: pin the shipped-this-week digest block (and the
"do not propose already-shipped work" instruction text) in the roadmap, Pest
Control, and Spackle exploration prompts.

Follows ``test_board_program_nothing_to_propose_prompts.py``'s pattern:
instantiate ``AgentOrchestrator`` via ``__new__``, call the prompt builder
with a ``digest_context`` argument, assert the digest block + instruction
appear in the returned string.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

from roboco.runtime.orchestrator import AgentOrchestrator, _shipped_digest_block

_DIGEST = (
    "Completed this week:\n"
    "- Add findings ledger (roboco-api, backend)\n"
    "\n"
    "CHANGELOG.md Unreleased section:\n"
    "### Added\n- New thing\n"
)

_INSTRUCTION_MARKER = "do not propose already-shipped work"


def _make_orch() -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    cast("Any", orch)._pm_respawn_tracker = {}
    return orch


def _bare_task(source: str) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "status": "pending",
        "team": "board",
        "title": "exploration cycle",
        "description": "x",
        "source": source,
        "orchestration_markers": None,
    }


def test_shipped_digest_block_renders_section_and_instruction() -> None:
    """The ``_shipped_digest_block`` helper renders the section header, the
    digest content, and the instruction text when a digest is present."""
    block = _shipped_digest_block(_DIGEST)
    assert "## Shipped-this-week digest" in block
    assert "Add findings ledger" in block
    assert _INSTRUCTION_MARKER in block


def test_shipped_digest_block_empty_when_no_digest() -> None:
    """No digest => no block — the section is omitted entirely, not rendered
    as an empty header."""
    assert _shipped_digest_block("") == ""


def test_roadmap_prompt_carries_shipped_digest() -> None:
    orch = _make_orch()
    prompt = orch._build_roadmap_prompt(
        _bare_task("board_roadmap"), digest_context=_DIGEST
    )
    assert "## Shipped-this-week digest" in prompt
    assert "Add findings ledger" in prompt
    assert _INSTRUCTION_MARKER in prompt


def test_pest_control_prompt_carries_shipped_digest() -> None:
    orch = _make_orch()
    prompt = orch._build_pest_control_prompt(
        _bare_task("board_pest_control"), digest_context=_DIGEST
    )
    assert "## Shipped-this-week digest" in prompt
    assert "Add findings ledger" in prompt
    assert _INSTRUCTION_MARKER in prompt


def test_spackle_prompt_carries_shipped_digest() -> None:
    orch = _make_orch()
    prompt = orch._build_spackle_prompt(
        _bare_task("board_spackle"), digest_context=_DIGEST
    )
    assert "## Shipped-this-week digest" in prompt
    assert "Add findings ledger" in prompt
    assert _INSTRUCTION_MARKER in prompt


def test_prompts_omit_digest_section_when_empty() -> None:
    """When no digest is available the section is absent — no empty header,
    no orphan instruction line. The prompt still reads cleanly."""
    orch = _make_orch()
    roadmap = orch._build_roadmap_prompt(_bare_task("board_roadmap"))
    pest = orch._build_pest_control_prompt(_bare_task("board_pest_control"))
    spackle = orch._build_spackle_prompt(_bare_task("board_spackle"))
    for prompt in (roadmap, pest, spackle):
        assert "## Shipped-this-week digest" not in prompt
        assert _INSTRUCTION_MARKER not in prompt
