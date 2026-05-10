"""Task completeness rules — "ALL DETAILS MUST BE FILLED" mandate, encoded.

Single source of truth for which Task fields are required at which
lifecycle moment (create, delegate, claim, open_pr, i_am_done). Defense
in depth:
  1. Pydantic schemas reject under-filled requests at the boundary.
  2. Service-layer raises TaskCompletenessError on construction.
  3. Gateway returns Envelope.incomplete_input with field_hints (the
     "interrogation" pattern from spec §5.2.1).

The DENYLIST catches placeholder strings agents have used to evade the
spirit of the rule — including the exact phrase from the deleted
services/task.py:5061-5062 silent fallback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class FieldRule(StrEnum):
    NON_EMPTY_STRING = "non_empty_string"
    MIN_LENGTH = "min_length"
    NON_EMPTY_LIST = "non_empty_list"
    EXPLICITLY_DECLARED = "explicitly_declared"


@dataclass(frozen=True)
class FieldRequirement:
    field: str
    rule: FieldRule
    value: int | None = None
    hint: str = ""


@dataclass(frozen=True)
class CompletenessSpec:
    name: str
    requires: tuple[FieldRequirement, ...]


@dataclass(frozen=True)
class CompletenessResult:
    passed: bool
    missing: list[str] = field(default_factory=list)
    field_hints: dict[str, str] = field(default_factory=dict)


class TaskCompletenessError(Exception):
    """Raised by service-layer when a task fails completeness rules."""

    def __init__(
        self,
        missing: list[str],
        field_hints: dict[str, str] | None = None,
        message: str | None = None,
    ) -> None:
        self.missing = list(missing)
        self.field_hints = dict(field_hints or {})
        super().__init__(message or f"task missing required fields: {missing}")


# Denylist — exact placeholder strings rejected as known evasions.
DENYLIST_AC_PHRASES: frozenset[str] = frozenset(
    {
        "completed and reviewed by assignee",
        "task complete",
        "see description",
        "see title",
        "tbd",
        "todo",
    }
)

DENYLIST_DESCRIPTION_PATTERNS: tuple[str, ...] = (
    r"^see title$",
    r"^same as title$",
    r"^todo$",
    r"^tbd$",
    r"^n/?a$",
    r"^pending$",
    r"^placeholder$",
)


_HINT_DESCRIPTION = (
    "1-2 sentence summary of the change and why it's needed (e.g. "
    "'Add /v1/orders endpoint returning paginated orders for the dashboard')."
)
_HINT_ACCEPTANCE_CRITERIA = (
    "non-empty list[str]; each item describes a verifiable outcome (e.g. "
    "'returns 401 when token absent'). Do NOT use placeholder strings like "
    "'completed and reviewed by assignee' — that's a known evasion phrase the "
    "gateway rejects."
)
_HINT_TASK_TYPE = (
    "one of: code | documentation | research | planning | design | administrative"
)
_HINT_NATURE = "one of: technical | bugfix | feature | refactor | docs"
_HINT_ESTIMATED_COMPLEXITY = (
    "one of: low | medium | high | critical, based on file count + dependency "
    "depth + novelty (low = 1-2 files, medium = 3-10 files or new module, "
    "high = cross-cell or schema-touching, critical = security or migration)"
)
_HINT_TEAM = (
    "one of: backend | frontend | ux_ui (for cell-routed work) | board | main_pm"
)
_HINT_TITLE = "single line, <= 200 chars, descriptive"


TASK_AT_CREATE: CompletenessSpec = CompletenessSpec(
    name="task_at_create",
    requires=(
        FieldRequirement("title", FieldRule.MIN_LENGTH, 1, _HINT_TITLE),
        FieldRequirement("description", FieldRule.MIN_LENGTH, 20, _HINT_DESCRIPTION),
        FieldRequirement(
            "acceptance_criteria",
            FieldRule.NON_EMPTY_LIST,
            hint=_HINT_ACCEPTANCE_CRITERIA,
        ),
        FieldRequirement(
            "task_type", FieldRule.EXPLICITLY_DECLARED, hint=_HINT_TASK_TYPE
        ),
        FieldRequirement("nature", FieldRule.EXPLICITLY_DECLARED, hint=_HINT_NATURE),
        FieldRequirement(
            "estimated_complexity",
            FieldRule.EXPLICITLY_DECLARED,
            hint=_HINT_ESTIMATED_COMPLEXITY,
        ),
        FieldRequirement("team", FieldRule.EXPLICITLY_DECLARED, hint=_HINT_TEAM),
    ),
)


def _check_explicitly_declared(value: Any) -> tuple[bool, str | None]:
    if value is None:
        return False, "field is None / missing"
    return True, None


def _check_non_empty_string(value: Any) -> tuple[bool, str | None]:
    if not isinstance(value, str) or not value.strip():
        return False, "must be a non-empty string"
    return True, None


def _check_min_length(value: Any, minimum: int) -> tuple[bool, str | None]:
    if not isinstance(value, str):
        return False, f"must be a string of length >= {minimum}"
    stripped_len = len(value.strip())
    if stripped_len < minimum:
        return False, f"must be at least {minimum} chars (got {stripped_len})"
    return True, None


def _check_non_empty_list(value: Any) -> tuple[bool, str | None]:
    if not isinstance(value, list) or len(value) == 0:
        return False, "must be a non-empty list"
    return True, None


def _check_field(req: FieldRequirement, value: Any) -> tuple[bool, str | None]:
    """Return (passed, problem_description). problem_description is None on pass."""
    if req.rule is FieldRule.EXPLICITLY_DECLARED:
        return _check_explicitly_declared(value)
    if req.rule is FieldRule.NON_EMPTY_STRING:
        return _check_non_empty_string(value)
    if req.rule is FieldRule.MIN_LENGTH:
        return _check_min_length(value, req.value or 0)
    return _check_non_empty_list(value)


def _matches_denylist_ac(items: Any) -> bool:
    """True if any item in `items` is a denylisted placeholder phrase."""
    if not isinstance(items, list):
        return False
    return any(
        isinstance(item, str) and item.strip().lower() in DENYLIST_AC_PHRASES
        for item in items
    )


def _matches_denylist_description(text: Any) -> bool:
    """True if the description matches any denylist regex."""
    if not isinstance(text, str):
        return False
    text_stripped = text.strip().lower()
    return any(
        re.fullmatch(pattern, text_stripped)
        for pattern in DENYLIST_DESCRIPTION_PATTERNS
    )


def check(spec: CompletenessSpec, task: Any) -> CompletenessResult:
    """Run every requirement in `spec` against `task`. Return a CompletenessResult.

    `task` may be a Pydantic model, a dataclass, or any object with the
    expected attributes (used in tests via SimpleNamespace).
    """
    missing: list[str] = []
    field_hints: dict[str, str] = {}

    for req in spec.requires:
        value = getattr(task, req.field, None)

        # Field-level rule check.
        passed, _problem = _check_field(req, value)
        if not passed:
            missing.append(req.field)
            field_hints[req.field] = req.hint
            continue

        # Denylist checks (post-rule).
        if req.field == "acceptance_criteria" and _matches_denylist_ac(value):
            missing.append("acceptance_criteria")
            field_hints["acceptance_criteria"] = (
                "rejected: placeholder phrase from the legacy silent fallback. "
                + req.hint
            )
            continue
        if req.field == "description" and _matches_denylist_description(value):
            missing.append("description")
            field_hints["description"] = (
                "rejected: placeholder/empty phrase. " + req.hint
            )
            continue

    return CompletenessResult(
        passed=len(missing) == 0,
        missing=missing,
        field_hints=field_hints,
    )
