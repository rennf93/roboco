"""Render canonical lifecycle artifacts (markdown, JSON, prompt fragments).

Output is deterministic - two calls with the same spec produce the
same bytes. Consumed by `scripts/build_lifecycle_artifacts.py` which
writes the rendered artifacts to disk; CI gate `make lifecycle &&
git diff --exit-code` ensures the on-disk artifacts always match
the current spec.
"""

from __future__ import annotations

import json
from typing import Any

from roboco.foundation.policy.lifecycle import (
    _INTENT_VERBS,
    _STATUS_TRANSITIONS,
    CLAIM_RULES,
    IntentSpec,
    Role,
    StatusTransition,
)


def _composes_line(iv: IntentSpec) -> str:
    if iv.composes:
        return f"**Composes:** {' → '.join(iv.composes)}\n"
    return "**Composes:** (no atomic actions)\n"


def _intent_verb_section(iv: IntentSpec) -> list[str]:
    """Render a single intent-verb section as markdown lines."""
    roles = sorted(r.value for r in iv.allowed_roles)
    section = [
        f"## {iv.name}\n",
        f"{iv.description}\n",
        f"**Allowed roles:** {', '.join(roles)}\n",
        _composes_line(iv),
    ]
    if iv.side_effects:
        section.append(f"**Side effects:** {', '.join(iv.side_effects)}\n")
    if iv.extra_preconditions:
        keys = sorted(p.key for p in iv.extra_preconditions)
        section.append(f"**Preconditions:** {', '.join(keys)}\n")
    section.append("")
    return section


def render_intent_verbs_md() -> str:
    """One section per intent verb. Description + allowed_roles + composes."""
    lines = ["# Intent Verbs (gateway-facing surface)\n"]
    for name in sorted(_INTENT_VERBS):
        lines.extend(_intent_verb_section(_INTENT_VERBS[name]))
    return "\n".join(lines)


def _transition_roles_str(t: StatusTransition) -> str:
    if t.role_constraint:
        return ", ".join(sorted(r.value for r in t.role_constraint))
    return "any"


def _transition_sort_key(t: StatusTransition) -> tuple[str, str, str]:
    return (t.source.value, t.target.value, t.triggered_by_action)


def render_status_transitions_md() -> str:
    """Mirror of pre-gateway STATUS_TRANSITIONS.md - table view."""
    lines = [
        "# Status Transitions",
        "",
        "| Source | Target | Action | Roles |",
        "|--------|--------|--------|-------|",
    ]
    for t in sorted(_STATUS_TRANSITIONS, key=_transition_sort_key):
        lines.append(
            f"| {t.source.value} | {t.target.value} | "
            f"{t.triggered_by_action} | {_transition_roles_str(t)} |"
        )
    return "\n".join(lines) + "\n"


def _intent_to_panel_dict(iv: IntentSpec) -> dict[str, Any]:
    return {
        "name": iv.name,
        "description": iv.description,
        "allowed_roles": sorted(r.value for r in iv.allowed_roles),
        "composes": list(iv.composes),
        "side_effects": list(iv.side_effects),
    }


def _transition_to_panel_dict(t: StatusTransition) -> dict[str, Any]:
    roles = sorted(r.value for r in t.role_constraint) if t.role_constraint else None
    return {
        "source": t.source.value,
        "target": t.target.value,
        "action": t.triggered_by_action,
        "roles": roles,
    }


def render_panel_json() -> str:
    """JSON dump for the panel UI's lifecycle visualizer."""
    payload = {
        "intents": [
            _intent_to_panel_dict(iv)
            for iv in sorted(_INTENT_VERBS.values(), key=lambda x: x.name)
        ],
        "transitions": [
            _transition_to_panel_dict(t)
            for t in sorted(_STATUS_TRANSITIONS, key=_transition_sort_key)
        ],
        "claim_rules": {
            r.value: sorted(s.value for s in statuses)
            for r, statuses in sorted(CLAIM_RULES.items(), key=lambda x: x[0].value)
        },
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def render_agent_prompt_fragment(role_value: str) -> str:
    """Per-role prompt fragment listing the verbs the agent can call.

    Injected at the top of every agent's system prompt so the agent's
    perception of "what verbs exist" matches what the gateway actually
    accepts.
    """
    role = Role(role_value)
    intents = [iv for iv in _INTENT_VERBS.values() if role in iv.allowed_roles]
    intents.sort(key=lambda iv: iv.name)
    lines = [
        f"# Verbs available to your role ({role.value})",
        "",
        "These are the only verbs the gateway will accept from you. Calling any",
        "other verb will be rejected with a Decision telling you the right one.",
        "",
    ]
    for iv in intents:
        lines.append(f"- **{iv.name}**: {iv.description}")
    return "\n".join(lines) + "\n"
