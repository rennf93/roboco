"""roboco/foundation/policy/board_programs.py

Board Program registry — the pure shape of "a board role periodically
originates held work". One entry per program; the engine/loop consult
this instead of growing bespoke per-engine loops. Foundation purity:
stdlib only, no IO.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

WEEK_SECONDS = 7 * 24 * 3600


class TriggerKind(StrEnum):
    CRON = "cron"  # due when interval elapsed since last opened cycle
    METRIC = "metric"  # due when the engine's metric predicate fires
    EVENT = "event"  # opened explicitly by an event hook, never by the loop


@dataclass(frozen=True)
class BoardProgram:
    key: str
    role: str  # AgentRole value of the solo explorer
    trigger: TriggerKind
    source: str  # tasks.source marker the dispatcher routes on
    default_interval_seconds: int  # cron cadence when no override is configured
    max_items_per_cycle: int = 7
    # "project" (reads one repo, e.g. a future bug hunt) needs a per-project
    # opt-in to even run; "org" (reads the org's process/market, e.g. the
    # roadmap cycle) runs org-wide by default and is only ever excluded per
    # project as an OUTPUT target. See project_participates below.
    scope: str = "org"


PROGRAMS: dict[str, BoardProgram] = {
    p.key: p
    for p in (
        BoardProgram(
            key="roadmap",
            role="product_owner",
            trigger=TriggerKind.CRON,
            source="board_roadmap",
            default_interval_seconds=WEEK_SECONDS,
        ),
        BoardProgram(
            key="x_feature",
            role="head_marketing",
            trigger=TriggerKind.CRON,
            source="x_feature_exploration",
            # Mirrors Settings.x_feature_spotlight_interval_seconds' own
            # default (1 day) — see test_x_feature_default_interval_matches_
            # settings_field_default, which guards the two from drifting
            # apart again.
            default_interval_seconds=86400,
        ),
        BoardProgram(
            key="pest_control",
            role="product_owner",
            trigger=TriggerKind.CRON,
            source="board_pest_control",
            default_interval_seconds=WEEK_SECONDS,
            max_items_per_cycle=5,
            # The first project-scoped program (spec §4): it reads one repo's
            # findings ledger / rework history, so it needs a per-project
            # opt-in before a cycle is worth opening at all.
            scope="project",
        ),
        BoardProgram(
            key="periscope",
            role="head_marketing",
            trigger=TriggerKind.CRON,
            source="board_periscope",
            default_interval_seconds=WEEK_SECONDS,
            # It reads the market, not a repo (spec §4) — org scope, same as
            # roadmap: runs org-wide by default, never needs a per-project
            # opt-in to fire.
            scope="org",
        ),
        BoardProgram(
            key="coroner",
            role="auditor",
            trigger=TriggerKind.EVENT,
            source="board_coroner",
            # EVENT programs are never cron-due (see program_due below) — this
            # is a harmless placeholder, not a live cadence. Coroner's cycles
            # open only via its event hooks (bounce >= 3 / cancel-after-work /
            # budget-block), never the loop.
            default_interval_seconds=WEEK_SECONDS,
            max_items_per_cycle=1,
            # Org-scoped (spec §4): it autopsies one incident task from
            # whichever project it landed in, not a repo it reads on a
            # schedule — no per-project opt-in gate.
            scope="org",
        ),
        BoardProgram(
            key="sentinel",
            role="auditor",
            trigger=TriggerKind.CRON,
            source="board_sentinel",
            default_interval_seconds=WEEK_SECONDS,
            # It reads org-wide drift signals (waivers, findings ledger,
            # conventions violations, budget), not one repo (spec §4) — org
            # scope, same as periscope.
            scope="org",
        ),
        BoardProgram(
            key="spackle",
            role="product_owner",
            trigger=TriggerKind.CRON,
            source="board_spackle",
            default_interval_seconds=2 * WEEK_SECONDS,
            max_items_per_cycle=5,
            # Gap-fill audit of one repo's half-shipped surface area (spec
            # §4) — project-scoped, same as pest_control.
            scope="project",
        ),
    )
}


def program_due(
    program: BoardProgram,
    *,
    now: datetime,
    last_opened_at: datetime | None,
    interval_override: int | None,
) -> bool:
    """Cron-due check. METRIC/EVENT programs are opened by their own hooks."""
    if program.trigger is not TriggerKind.CRON:
        return False
    if last_opened_at is None:
        return True
    interval = interval_override or program.default_interval_seconds
    return (now - last_opened_at).total_seconds() >= interval


def project_participates(
    program: BoardProgram, board_programs_field: list[str] | None
) -> bool:
    """Whether ``program`` runs/outputs against a project carrying this field.

    Dual polarity (CEO, 2026-07-24): a ``scope="project"`` program (reads one
    repo) is affirmative opt-in — True iff its key is listed; null/absent is
    OUT. A ``scope="org"`` program (reads the org's process/market) is
    default-eligible — True unless ``"!{key}"`` is listed; null/absent is IN,
    preserving parity for programs migrated onto the registry.
    """
    field = board_programs_field or []
    if program.scope == "project":
        return program.key in field
    return f"!{program.key}" not in field


def validate_board_programs_field(
    value: list[str] | None,
    *,
    programs: dict[str, BoardProgram] | None = None,
) -> list[str] | None:
    """Validate a ``projects.board_programs`` entry list.

    Each entry is either a known program key (plain — the project-scoped
    opt-in form) or an org-scoped key prefixed with ``!`` (the org-scoped
    opt-out form). Raises ``ValueError`` on an unknown key, a ``!`` prefix on
    a project-scoped key (meaningless — a project-scoped program's default is
    already excluded, so there is nothing to opt out of), or a plain key on
    an org-scoped key (meaningless the other way — an org-scoped program
    already runs against every project by default, so there is nothing to
    opt into; ``project_participates`` never consults a plain entry for it).
    """
    if value is None:
        return None
    registry = PROGRAMS if programs is None else programs
    for entry in value:
        excluding = entry.startswith("!")
        key = entry[1:] if excluding else entry
        program = registry.get(key)
        if program is None:
            raise ValueError(f"unknown board program key {key!r}")
        if excluding and program.scope != "org":
            raise ValueError(
                f"'!{key}' is meaningless on project-scoped program {key!r} — "
                "its default is already excluded"
            )
        if not excluding and program.scope == "org":
            raise ValueError(
                f"{key!r} is meaningless on org-scoped program {key!r} — a "
                "plain key would opt in, but org-scoped programs already run "
                f"by default; use '!{key}' to exclude this project instead"
            )
    return list(value)
