"""tests/unit/foundation/test_board_programs.py"""

from datetime import UTC, datetime, timedelta

import pytest
from roboco.config import Settings
from roboco.foundation.policy.board_programs import (
    PROGRAMS,
    WEEK_SECONDS,
    BoardProgram,
    TriggerKind,
    program_due,
    project_participates,
    validate_board_programs_field,
)


def test_x_feature_default_interval_matches_settings_field_default() -> None:
    """Guards the registry cadence and the live due-check's cadence
    (``Settings.x_feature_spotlight_interval_seconds``) from drifting apart
    again — reads the pydantic field default, never a live env-configured
    instance, so this can't pass by accident in a differently-configured
    environment."""
    field_default = Settings.model_fields["x_feature_spotlight_interval_seconds"]
    assert PROGRAMS["x_feature"].default_interval_seconds == field_default.default


def test_registry_carries_the_two_migrated_programs() -> None:
    assert {"roadmap", "x_feature"} <= set(PROGRAMS)
    rm = PROGRAMS["roadmap"]
    assert rm.role == "product_owner"
    assert rm.source == "board_roadmap"
    assert rm.trigger is TriggerKind.CRON
    xf = PROGRAMS["x_feature"]
    assert xf.role == "head_marketing"
    assert xf.source == "x_feature_exploration"


_PEST_CONTROL_MAX_ITEMS_PER_CYCLE = 5


def test_registry_carries_pest_control() -> None:
    pc = PROGRAMS["pest_control"]
    assert pc.role == "product_owner"
    assert pc.source == "board_pest_control"
    assert pc.trigger is TriggerKind.CRON
    assert pc.scope == "project"
    assert pc.max_items_per_cycle == _PEST_CONTROL_MAX_ITEMS_PER_CYCLE


def test_registry_carries_periscope() -> None:
    p = PROGRAMS["periscope"]
    assert p.role == "head_marketing"
    assert p.source == "board_periscope"
    assert p.trigger is TriggerKind.CRON
    assert p.scope == "org"
    assert p.default_interval_seconds == WEEK_SECONDS


_SCALES_MAX_ITEMS_PER_CYCLE = 7


def test_registry_carries_scales() -> None:
    s = PROGRAMS["scales"]
    assert s.role == "product_owner"
    assert s.source == "board_scales"
    assert s.trigger is TriggerKind.CRON
    assert s.scope == "org"
    assert s.max_items_per_cycle == _SCALES_MAX_ITEMS_PER_CYCLE
    assert s.default_interval_seconds == 30 * 24 * 3600


def test_registry_carries_coroner() -> None:
    c = PROGRAMS["coroner"]
    assert c.role == "auditor"
    assert c.source == "board_coroner"
    assert c.trigger is TriggerKind.EVENT
    assert c.scope == "org"


def test_coroner_is_never_cron_due() -> None:
    """An EVENT program is never cron-due regardless of how long it's been
    since the last cycle opened — mirrors test_program_due_event_never_cron_
    fires but against the real registered entry."""
    assert not program_due(
        PROGRAMS["coroner"],
        now=datetime(2026, 7, 24, tzinfo=UTC),
        last_opened_at=None,
        interval_override=None,
    )


def test_registry_carries_sentinel() -> None:
    s = PROGRAMS["sentinel"]
    assert s.role == "auditor"
    assert s.source == "board_sentinel"
    assert s.trigger is TriggerKind.CRON
    assert s.scope == "org"
    assert s.default_interval_seconds == WEEK_SECONDS


_SPACKLE_MAX_ITEMS_PER_CYCLE = 5


def test_registry_carries_spackle() -> None:
    sp = PROGRAMS["spackle"]
    assert sp.role == "product_owner"
    assert sp.source == "board_spackle"
    assert sp.trigger is TriggerKind.CRON
    assert sp.scope == "project"
    assert sp.max_items_per_cycle == _SPACKLE_MAX_ITEMS_PER_CYCLE
    assert sp.default_interval_seconds == 2 * WEEK_SECONDS


_MIRROR_MAX_ITEMS_PER_CYCLE = 5
_QUARTER_SECONDS = 90 * 24 * 3600


def test_registry_carries_mirror() -> None:
    m = PROGRAMS["mirror"]
    assert m.role == "head_marketing"
    assert m.source == "board_mirror"
    assert m.trigger is TriggerKind.CRON
    assert m.scope == "project"
    assert m.max_items_per_cycle == _MIRROR_MAX_ITEMS_PER_CYCLE
    assert m.default_interval_seconds == _QUARTER_SECONDS


def test_registry_carries_megaphone() -> None:
    mg = PROGRAMS["megaphone"]
    assert mg.role == "head_marketing"
    assert mg.source == "board_megaphone"
    assert mg.trigger is TriggerKind.CRON
    assert mg.scope == "org"
    assert mg.default_interval_seconds == 3 * 24 * 3600


_LIBRARIAN_MAX_ITEMS_PER_CYCLE = 3


def test_registry_carries_librarian() -> None:
    p = PROGRAMS["librarian"]
    assert p.role == "auditor"
    assert p.source == "board_librarian"
    assert p.trigger is TriggerKind.CRON
    assert p.scope == "org"
    assert p.max_items_per_cycle == _LIBRARIAN_MAX_ITEMS_PER_CYCLE
    assert p.default_interval_seconds == 2 * WEEK_SECONDS


_WAR_ROOM_MAX_POSTS_PER_CAMPAIGN = 6


def test_registry_carries_war_room() -> None:
    wr = PROGRAMS["war_room"]
    assert wr.role == "head_marketing"
    assert wr.source == "board_war_room"
    assert wr.trigger is TriggerKind.EVENT
    assert wr.scope == "org"
    assert wr.max_items_per_cycle == _WAR_ROOM_MAX_POSTS_PER_CAMPAIGN


def test_war_room_is_never_cron_due() -> None:
    """An EVENT program is never cron-due regardless of how long it's been
    since the last cycle opened — mirrors test_coroner_is_never_cron_due.
    Unlike Coroner, War Room's ``_ORIGINATORS`` entry is a REAL originator
    (see test_board_program_engine.py), so this test is what actually proves
    the loop still never drives it — the trigger-kind guard, not a stub."""
    assert not program_due(
        PROGRAMS["war_room"],
        now=datetime(2026, 7, 24, tzinfo=UTC),
        last_opened_at=None,
        interval_override=None,
    )


_DOGFOOD_MAX_ITEMS_PER_CYCLE = 5


def test_registry_carries_dogfood() -> None:
    d = PROGRAMS["dogfood"]
    assert d.role == "product_owner"
    assert d.source == "board_dogfood"
    assert d.trigger is TriggerKind.EVENT
    assert d.scope == "project"
    assert d.max_items_per_cycle == _DOGFOOD_MAX_ITEMS_PER_CYCLE


def test_dogfood_is_never_cron_due() -> None:
    """An EVENT program is never cron-due regardless of how long it's been
    since the last cycle opened — mirrors test_coroner_is_never_cron_due,
    but against Dogfood: unlike Coroner it DOES have a real originator (see
    roboco.services.board_programs._originate_dogfood), so this asserts the
    cron loop's own gate refuses it, not that no originator exists."""
    assert not program_due(
        PROGRAMS["dogfood"],
        now=datetime(2026, 7, 24, tzinfo=UTC),
        last_opened_at=None,
        interval_override=None,
    )


_BARFLY_MAX_ITEMS_PER_CYCLE = 5


def test_registry_carries_barfly() -> None:
    b = PROGRAMS["barfly"]
    assert b.role == "head_marketing"
    assert b.source == "board_barfly"
    assert b.trigger is TriggerKind.CRON
    assert b.scope == "org"
    assert b.max_items_per_cycle == _BARFLY_MAX_ITEMS_PER_CYCLE
    assert b.default_interval_seconds == 2 * 24 * 3600


def test_registry_carries_fourteen_programs() -> None:
    """Locks the union so a future addition/removal is deliberate — matches
    the count-whatever-your-base-has-plus-war_room shape the other
    registry-parity tests already exercise per-key."""
    assert set(PROGRAMS) == {
        "roadmap",
        "x_feature",
        "pest_control",
        "periscope",
        "coroner",
        "sentinel",
        "spackle",
        "scales",
        "mirror",
        "megaphone",
        "librarian",
        "war_room",
        "barfly",
        "dogfood",
    }


_MIN_DESCRIPTION_CHARS = 40


def test_every_registry_entry_has_human_title_and_description() -> None:
    """The panel renders title/description, never the raw key — a registry
    entry shipping without them regresses the card to unreadable keys."""
    for key, program in PROGRAMS.items():
        assert program.title.strip(), f"{key} has no title"
        assert len(program.description.strip()) >= _MIN_DESCRIPTION_CHARS, (
            f"{key} description too thin"
        )
    titles = [p.title for p in PROGRAMS.values()]
    assert len(set(titles)) == len(titles), "duplicate program titles"


def test_program_due_cron_interval() -> None:
    now = datetime(2026, 7, 24, tzinfo=UTC)
    p = PROGRAMS["roadmap"]
    assert program_due(p, now=now, last_opened_at=None, interval_override=None)
    recent = now - timedelta(seconds=10)
    assert not program_due(p, now=now, last_opened_at=recent, interval_override=None)
    old = now - timedelta(seconds=p.default_interval_seconds + 1)
    assert program_due(p, now=now, last_opened_at=old, interval_override=None)


def test_program_due_event_never_cron_fires() -> None:
    p = BoardProgram(
        key="k",
        role="auditor",
        trigger=TriggerKind.EVENT,
        source="s",
        default_interval_seconds=0,
    )
    assert not program_due(
        p,
        now=datetime(2026, 7, 24, tzinfo=UTC),
        last_opened_at=None,
        interval_override=None,
    )


def test_program_due_interval_override_wins_over_default() -> None:
    now = datetime(2026, 7, 24, tzinfo=UTC)
    p = PROGRAMS["roadmap"]
    recent = now - timedelta(seconds=100)
    # Default interval (a week) would still block; a short override fires.
    assert program_due(p, now=now, last_opened_at=recent, interval_override=50)
    assert not program_due(p, now=now, last_opened_at=recent, interval_override=200)


# ---------------------------------------------------------------------------
# Task 6b: per-project program scoping
# ---------------------------------------------------------------------------


def test_registry_entries_default_to_org_scope() -> None:
    assert PROGRAMS["roadmap"].scope == "org"
    assert PROGRAMS["x_feature"].scope == "org"


_PROJECT_PROGRAM = BoardProgram(
    key="pest_control",
    role="product_owner",
    trigger=TriggerKind.CRON,
    source="board_pest_control",
    default_interval_seconds=7 * 24 * 3600,
    scope="project",
)
_ORG_PROGRAM = PROGRAMS["roadmap"]  # scope="org"


def test_project_scoped_program_is_affirmative_opt_in() -> None:
    assert not project_participates(_PROJECT_PROGRAM, None)
    assert not project_participates(_PROJECT_PROGRAM, [])
    assert not project_participates(_PROJECT_PROGRAM, ["some_other_key"])
    assert project_participates(_PROJECT_PROGRAM, ["pest_control"])


def test_org_scoped_program_is_default_eligible_opt_out() -> None:
    assert project_participates(_ORG_PROGRAM, None)
    assert project_participates(_ORG_PROGRAM, [])
    assert project_participates(_ORG_PROGRAM, ["some_other_key"])
    assert not project_participates(_ORG_PROGRAM, ["!roadmap"])


def test_validate_board_programs_field_accepts_none() -> None:
    assert validate_board_programs_field(None) is None


def test_validate_board_programs_field_accepts_known_org_exclusion() -> None:
    assert validate_board_programs_field(["!roadmap"]) == ["!roadmap"]


def test_validate_board_programs_field_rejects_plain_key_on_org_scoped_program() -> (
    None
):
    """Both registered programs are org-scoped today, so a plain "roadmap"
    entry (the project-scoped opt-in form) is meaningless — org-scoped
    programs run against every project by default and are only ever
    excluded via '!key'. See test_validate_board_programs_field_allows_
    plain_project_scoped_key below for the positive case on a synthetic
    project-scoped program."""
    with pytest.raises(ValueError, match="meaningless"):
        validate_board_programs_field(["roadmap"])


def test_validate_board_programs_field_rejects_unknown_key() -> None:
    with pytest.raises(ValueError, match="unknown board program key"):
        validate_board_programs_field(["not_a_real_program"])


def test_validate_board_programs_field_rejects_unknown_excluded_key() -> None:
    with pytest.raises(ValueError, match="unknown board program key"):
        validate_board_programs_field(["!not_a_real_program"])


def test_validate_board_programs_field_rejects_bang_on_project_scoped_key() -> None:
    registry = {"pest_control": _PROJECT_PROGRAM}
    with pytest.raises(ValueError, match="meaningless"):
        validate_board_programs_field(["!pest_control"], programs=registry)


def test_validate_board_programs_field_allows_plain_project_scoped_key() -> None:
    registry = {"pest_control": _PROJECT_PROGRAM}
    assert validate_board_programs_field(["pest_control"], programs=registry) == [
        "pest_control"
    ]
