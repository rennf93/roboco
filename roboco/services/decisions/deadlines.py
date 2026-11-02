"""How long each pilot's caller may wait for a verdict.

A Decisions call blocks whoever awaits it: an agent's verb, the dispatcher
loop, the health loop. On 2026-10-28 the 27B Clef took up to its 60 s
timeout on every call and agents' verbs waited for it. Each ON verdict now
carries a deadline sized to its caller, and the sidecar refuses at once an
input that cannot finish before it (no verdict, the caller's baseline).
SHADOW verdicts never make a caller wait: they run in the background on
the background deadline (``decide_for_pilot``).

The default is the inline deadline, safe inside any verb or shared loop; a
pilot gets more time only where its caller can afford it.
"""

from __future__ import annotations

from roboco.config import settings

# The verdicts an agent asks for itself through /api/v1/do: answered under
# the agent's 30 s client timeout.
_AGENT_TOOL_PILOTS = frozenset({"preflight_diff", "triage_failure"})

# Inside i_am_done / submit_up / submit_root (1500 s walls), with a diff in
# the state.
_LONG_VERB_PILOTS = frozenset({"findings_mapping"})

# Off every request and shared loop: fire-and-forget tasks or an engine's
# own loop, where waiting delays nothing else.
_BACKGROUND_PILOTS = frozenset(
    {
        "board_due_early",
        "changelog_highlights",
        "ci_watch_route",
        "decision_note_sufficiency",
        "dep_update_risk",
        "external_pr_triage",
        "heal_severity",
        "memory_distill_gate",
        "proactive_domain",
        "release_worthy",
        "self_heal",
        "stranded_response",
        "tg_freetext_gate",
        "vault_prefilter",
        "x_mention_triage",
    }
)


def pilot_deadline_s(pilot: str) -> float:
    """Seconds ``pilot``'s caller waits for an ON verdict."""
    if pilot in _AGENT_TOOL_PILOTS:
        return settings.decisions_agent_tool_deadline_s
    if pilot in _LONG_VERB_PILOTS:
        return settings.decisions_long_verb_deadline_s
    if pilot in _BACKGROUND_PILOTS:
        return settings.decisions_background_deadline_s
    return settings.decisions_inline_deadline_s
