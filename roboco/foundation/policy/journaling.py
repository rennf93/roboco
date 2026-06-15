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

from roboco.foundation.identity import Role
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


class ReadTier(StrEnum):
    """How widely a role can read other agents' journals."""

    OWN = "own"  # only my own
    CELL = "cell"  # my cell only
    CELL_AND_PMS = "cell_and_pms"  # my cell + PM chain
    ALL_CELLS = "all_cells"  # every cell (for cross-cell roles)
    ALL = "all"  # every journal (auditor / CEO)


ROLE_READ_TIERS: dict[Role, ReadTier] = {
    Role.SYSTEM: ReadTier.OWN,
    Role.DEVELOPER: ReadTier.CELL,
    Role.QA: ReadTier.CELL,
    Role.DOCUMENTER: ReadTier.CELL,
    Role.CELL_PM: ReadTier.CELL_AND_PMS,
    Role.MAIN_PM: ReadTier.ALL_CELLS,
    Role.PRODUCT_OWNER: ReadTier.ALL_CELLS,
    Role.HEAD_MARKETING: ReadTier.ALL_CELLS,
    Role.AUDITOR: ReadTier.ALL,
    # Intake interviewer is isolated — talks only to the human, reads only its
    # own journal.
    Role.PROMPTER: ReadTier.OWN,
    # Secretary advises the CEO — reads everything to give an informed picture.
    Role.SECRETARY: ReadTier.ALL,
    Role.CEO: ReadTier.ALL,
}


# Slugs whose journals are "protected" — only the agent themselves can read.
# Pre-gateway: enforcement/journal_perms.PROTECTED_JOURNALS.
PROTECTED_JOURNALS: frozenset[str] = frozenset({"ceo", "auditor"})
