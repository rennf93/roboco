"""Every Board Program exploration prompt must mention nothing_to_propose —
the explicit "this cycle found nothing worth proposing" exit. A prompt that
never names the verb is dead code: the explorer never learns it exists, so a
genuinely empty cycle would still call i_am_idle() on a PENDING task and wedge
the program's LEARN dedup forever (see ContentActions.nothing_to_propose).

Parametrized over ``PROGRAMS`` itself (not a hardcoded list) — a newly
registered program with no entry in ``_PROMPT_BUILDERS`` below fails loudly,
so this test cannot silently go stale as the registry grows.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import pytest
from roboco.foundation.policy.board_programs import PROGRAMS
from roboco.runtime.orchestrator import AgentOrchestrator

# program key -> the AgentOrchestrator prompt-builder method name. Every
# method here accepts a bare ``task`` dict (the LEARN/evidence context
# params all default to "").
_PROMPT_BUILDERS: dict[str, str] = {
    "roadmap": "_build_roadmap_prompt",
    "x_feature": "_build_feature_spotlight_prompt",
    "pest_control": "_build_pest_control_prompt",
    "periscope": "_build_periscope_prompt",
    "coroner": "_build_coroner_prompt",
    "sentinel": "_build_sentinel_prompt",
    "spackle": "_build_spackle_prompt",
    "scales": "_build_scales_prompt",
    "mirror": "_build_mirror_prompt",
    "megaphone": "_build_megaphone_prompt",
    "librarian": "_build_librarian_prompt",
    "war_room": "_build_war_room_prompt",
    "barfly": "_build_barfly_prompt",
    "dogfood": "_build_dogfood_prompt",
}


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


@pytest.mark.parametrize("program_key", sorted(PROGRAMS))
def test_every_board_program_prompt_mentions_nothing_to_propose(
    program_key: str,
) -> None:
    method_name = _PROMPT_BUILDERS.get(program_key)
    assert method_name is not None, (
        f"board program {program_key!r} has no entry in _PROMPT_BUILDERS — "
        "wire its exploration prompt to mention nothing_to_propose() and add "
        "it here"
    )
    orch = _make_orch()
    builder = getattr(orch, method_name)
    program = PROGRAMS[program_key]
    prompt = builder(_bare_task(program.source))
    assert "nothing_to_propose" in prompt, (
        f"{method_name} never mentions nothing_to_propose() — a genuinely "
        "empty cycle has no documented exit"
    )


def test_prompt_builders_cover_exactly_the_registry() -> None:
    assert set(_PROMPT_BUILDERS) == set(PROGRAMS)
