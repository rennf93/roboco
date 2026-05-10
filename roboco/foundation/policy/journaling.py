"""Journaling foundation — scope catalog + scope→type mapping.

The 5 scopes the panel UI exposes (Notes/Decisions/Reflections/Learnings/
Struggles) are first-class. Each scope must be wired to at least one verb's
required-set in foundation.policy.tracing.VERB_REQUIREMENTS — that wiring
keeps the scope catalog from drifting into "documented but unused".

Replaces:
  - services/gateway/content_actions._VALID_NOTE_SCOPES (frozenset of strings)
  - services/journal._SCOPE_TO_TYPE (scope-string → JournalEntryType)
"""

from __future__ import annotations

from enum import StrEnum

from roboco.models.base import JournalEntryType


class Scope(StrEnum):
    """Journal entry scope — agent-facing string values."""

    NOTE = "note"  # Quick observation, not load-bearing
    DECISION = "decision"  # Reasoning before an action; PM scope rationale
    REFLECT = "reflect"  # End-of-task / end-of-merge retrospection
    LEARNING = "learning"  # Pattern worth surfacing to the team
    STRUGGLE = "struggle"  # Stuck point, before / instead of i_am_blocked


# Scope → SQLAlchemy entry type. Single source for the mapping.
SCOPE_TO_TYPE: dict[Scope, JournalEntryType] = {
    Scope.NOTE: JournalEntryType.GENERAL,
    Scope.DECISION: JournalEntryType.DECISION_LOG,
    Scope.REFLECT: JournalEntryType.TASK_REFLECTION,
    Scope.LEARNING: JournalEntryType.LEARNING,
    Scope.STRUGGLE: JournalEntryType.STRUGGLE,
}
