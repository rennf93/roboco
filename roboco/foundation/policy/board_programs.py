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
MONTH_SECONDS = 30 * 24 * 3600
QUARTER_SECONDS = 90 * 24 * 3600
DAY_SECONDS = 24 * 3600


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
    # Human-facing name + one-sentence "what does enabling/running this do"
    # — the panel renders these, never the raw key. Registry entries MUST
    # fill both (test-enforced); the defaults exist only so synthetic
    # test programs stay one-liners.
    title: str = ""
    description: str = ""


PROGRAMS: dict[str, BoardProgram] = {
    p.key: p
    for p in (
        BoardProgram(
            key="roadmap",
            role="product_owner",
            trigger=TriggerKind.CRON,
            source="board_roadmap",
            default_interval_seconds=WEEK_SECONDS,
            title="Roadmap Cycle",
            description=(
                "Weekly: the Product Owner explores the charter, releases, and "
                "metrics, then proposes a themed cycle of roadmap items for "
                "your per-item approval — approved items land in the backlog."
            ),
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
            title="Feature Spotlight",
            description=(
                "Daily check: the Head of Marketing investigates what actually "
                "shipped and drafts one X post spotlighting an under-publicized "
                "feature, held in the X queue for your approval."
            ),
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
            title="Pest Control",
            description=(
                "Weekly bug hunt (accelerated when the rework rate spikes): "
                "the Product Owner mines the findings ledger and rework "
                "hotspots of an opted-in project and proposes up to five "
                "evidence-backed bug tasks for your per-item approval."
            ),
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
            title="Periscope",
            description=(
                "Weekly market brief: the Head of Marketing researches "
                "competitors and adjacent tools (every claim source-cited) and "
                "files a read-only report that also feeds the next roadmap "
                "exploration."
            ),
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
            title="Coroner",
            description=(
                "Event-triggered postmortem: when a task bounces three times, "
                "is cancelled after work started, or blows its budget, the "
                "Auditor autopsies it and proposes one process change — often "
                "a draft playbook for the curation queue."
            ),
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
            title="Sentinel",
            description=(
                "Weekly drift watch: the Auditor reviews waiver trends, open "
                "findings, and budget anomalies and files a read-only "
                "state-of-quality report naming systemic issues."
            ),
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
            title="Spackle",
            description=(
                "Biweekly gap-fill audit: the Product Owner hunts an opted-in "
                "project's half-shipped surface area — routes without UI, "
                "flags without docs, dead-end tabs — and proposes fixes for "
                "your per-item approval."
            ),
        ),
        BoardProgram(
            key="scales",
            role="product_owner",
            trigger=TriggerKind.CRON,
            source="board_scales",
            default_interval_seconds=MONTH_SECONDS,
            max_items_per_cycle=7,
            # Org-scoped (spec §4): it reviews the LIVE portfolio across every
            # project against the charter, not one repo's own findings ledger
            # — no per-project opt-in gate, mirrors periscope/roadmap.
            scope="org",
            title="Scales",
            description=(
                "Monthly portfolio rebalance: the Product Owner reviews stale "
                "backlog against the charter and proposes re-prioritizations "
                "and cancellations — approving an item executes the change on "
                "the live task."
            ),
        ),
        BoardProgram(
            key="mirror",
            role="head_marketing",
            trigger=TriggerKind.CRON,
            source="board_mirror",
            default_interval_seconds=QUARTER_SECONDS,
            max_items_per_cycle=5,
            # Positioning audit of one repo's messaging surfaces (README,
            # docs-site, website) against the charter + shipped reality (spec
            # §4) — project-scoped, same as pest_control/spackle.
            scope="project",
            title="Mirror",
            description=(
                "Quarterly positioning audit: the Head of Marketing checks an "
                "opted-in project's README and docs claims against shipped "
                "reality and proposes documentation fixes for your per-item "
                "approval."
            ),
        ),
        BoardProgram(
            key="megaphone",
            role="head_marketing",
            trigger=TriggerKind.CRON,
            source="board_megaphone",
            default_interval_seconds=3 * 24 * 3600,  # 3 days
            # The standing editorial calendar (spec §4): dev-log threads,
            # behind-the-scenes posts, changelog highlights — it reads the
            # org's own shipped-task/changelog history, not a repo it audits
            # on a schedule. Org scope, same as periscope/x_feature.
            scope="org",
            title="Megaphone",
            description=(
                "Standing editorial calendar (every 3 days): the Head of "
                "Marketing drafts a dev-log or changelog-highlight post from "
                "what the fleet actually shipped, held in the X queue for your "
                "approval."
            ),
        ),
        BoardProgram(
            key="librarian",
            role="auditor",
            trigger=TriggerKind.CRON,
            source="board_librarian",
            default_interval_seconds=2 * WEEK_SECONDS,
            max_items_per_cycle=3,
            # Org-scoped (spec §4): it mines journals/learnings org-wide, not
            # one repo — no per-project opt-in gate, mirrors sentinel/periscope.
            scope="org",
            title="Librarian",
            description=(
                "Biweekly playbook mining: the Auditor turns recurring lessons "
                "from agent journals into up to three draft playbooks for the "
                "normal curation queue."
            ),
        ),
        BoardProgram(
            key="war_room",
            role="head_marketing",
            trigger=TriggerKind.EVENT,
            source="board_war_room",
            # EVENT programs are never cron-due (see program_due below) — this
            # is a harmless placeholder, not a live cadence. War Room's cycles
            # open only via the release-publish hook or a CEO "run now" call,
            # never the loop.
            default_interval_seconds=WEEK_SECONDS,
            max_items_per_cycle=6,  # a campaign's post cap (2-6, spec §4)
            # Org-scoped (spec §4): a campaign is about a release or an
            # on-demand marketing push, not one repo's own state — no
            # per-project opt-in gate.
            scope="org",
            title="War Room",
            description=(
                "Release campaigns: on publish (or run-now) the Head of "
                "Marketing designs a 2-6 post arc with recommended timing; "
                "each post is a held X draft you approve at its moment — "
                "nothing auto-posts."
            ),
        ),
        BoardProgram(
            key="barfly",
            role="head_marketing",
            trigger=TriggerKind.CRON,
            source="board_barfly",
            default_interval_seconds=2 * DAY_SECONDS,
            max_items_per_cycle=5,
            # Org-scoped (spec §4): it searches X for adjacent conversations,
            # not a repo — no per-project opt-in gate, mirrors periscope.
            scope="org",
            title="Barfly",
            description=(
                "Every two days: the Head of Marketing searches X for relevant "
                "conversations RoboCo isn't in (screened for prompt injection) "
                "and drafts replies, held in the X queue for your approval."
            ),
        ),
        BoardProgram(
            key="dogfood",
            role="product_owner",
            trigger=TriggerKind.EVENT,
            source="board_dogfood",
            # EVENT programs are never cron-due (see program_due below) — this
            # is a harmless placeholder, not a live cadence, mirroring
            # coroner's. Unlike coroner, a Dogfood cycle DOES have a real
            # ``_ORIGINATORS`` entry (see roboco.services.board_programs) —
            # it needs no external incident id to target, just the next
            # opted-in project in rotation, so both the release-publish hook
            # and a CEO "run now" can open a cycle through the ordinary
            # ``BoardProgramEngine.open_program_cycle`` path.
            default_interval_seconds=WEEK_SECONDS,
            max_items_per_cycle=5,
            # Project-scoped (spec §3/§4): it walks one project's live
            # surfaces (panel, docs site) as a user, same as
            # pest_control/spackle/mirror.
            scope="project",
            title="Dogfood",
            description=(
                "After a release (or run-now): the Product Owner walks an "
                "opted-in project's live surfaces like a user — browser tools, "
                "panel, docs — and files UX-friction tasks for your per-item "
                "approval."
            ),
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
