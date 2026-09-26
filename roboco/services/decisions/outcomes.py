"""Decision outcome labels: the shared vocabulary between outcome
producers and the training exporter.

An outcome producer (code that later learns what ACTUALLY happened after
a decision - e.g. the self-heal engine's recurrence check) attaches a
short slug to decision_log rows via ``persist.record_outcome``. This
module is the single registry saying what each slug asserts about the
pilot's gate question, so the exporter can turn a labeled row into a
fine-tuning gold label without either side guessing.

Doctrine: a slug not in the registry (or listed as unusable) is never
guessed at. The exporter skips it; the row keeps its inputs for future
labeling.
"""

from __future__ import annotations

from typing import Any

# Gold probability maps must sum to 1 within float-print rounding.
_GOLD_SUM_TOLERANCE = 1e-6

# Pilot slugs (decision_log.pilot values).
SELF_HEAL_PILOT = "self_heal"

# Pilot slugs (decision_log.pilot values).
SELF_HEAL_PILOT = "self_heal"
RESPAWN_VERDICT_PILOT = "respawn_verdict"

# Outcome slugs, grouped by the pilot that produces them. A slug is part
# of the training contract: renaming one silently orphans every row
# already labeled with it, so both sides import these constants.
CLEARED_AFTER_GATE = "cleared_after_gate"
STILL_FAILING_AFTER_WINDOW = "still_failing_after_window"
SUPERSEDED_BY_FIX_TASK = "superseded_by_fix_task"

# Task-trajectory slugs (the trajectory labeler, spec 12.1 Wave 1). One
# slug = one observed fate of the subject task; the gold asserts which of
# the pilot's options WAS true.
TASK_DELIVERED_AFTER = "task_delivered_after"
TASK_CANCELLED_AFTER = "task_cancelled_after"
TASK_STALLED_PAST_WINDOW = "task_stalled_past_window"
OWNED_TASK_SUBMITTED_AFTER = "owned_task_submitted_after"
OWNED_TASK_ROTTED_AFTER = "owned_task_rotted_after"
IDLE_WAIT_RESOLVED_AFTER = "idle_wait_resolved_after"
QA_PASSED_AFTER = "qa_passed_after"
PLAN_BOUNCED_AFTER = "plan_bounced_after"
PLAN_STALLED_PAST_WINDOW = "plan_stalled_past_window"
LIMIT_LIFTED_QUICKLY = "limit_lifted_quickly"
LIMIT_LIFTED_AFTER_COOLDOWN = "limit_lifted_after_cooldown"
RE_LIMITED_ON_RESUME = "re_limited_on_resume"
STALE_UNRESOLVED = "stale_unresolved"
FLAKE_CONFIRMED_LATER = "flake_confirmed_by_later_failures"
REGRESSION_CONFIRMED_LATER = "regression_confirmed_no_recurrence"

# Gold label shapes mirror the laya fine-tune corpus: per question key,
# a probability map over the question's outcomes.
#   noul:   {"true": p, "false": 1-p}   (the instruction's proposition)
#   choice: {option_key: p, ...}
#   score:  {"0": p, "1": p, ...}       (one level per legend index)
Gold = dict[str, dict[str, float]]

# (pilot, outcome slug) -> per-question gold for that pilot's gate row.
# Only question keys present in the map are exported: a row whose
# questions carry no derivable ground truth stays input-only.
OUTCOME_GOLD: dict[tuple[str, str], Gold] = {
    # self_heal (spec 6.1 noul gate: "This failure is transient
    # (infra/flake/network) rather than a defect in the recent commits.")
    # The breach cleared after the gate without a fix task staying open:
    # the transience claim held.
    (SELF_HEAL_PILOT, CLEARED_AFTER_GATE): {
        "gate": {"true": 1.0, "false": 0.0},
    },
    # The signal kept breaching a full outcome window after the gate: a
    # one-off flake would have cleared, so the claim did not hold.
    (SELF_HEAL_PILOT, STILL_FAILING_AFTER_WINDOW): {
        "gate": {"true": 0.0, "false": 1.0},
    },
    # idle_legitimacy (B34 choice over the owned tasks' fate after the
    # idle). Exactly one option can be gold: the fate the tasks actually
    # took proves which option WAS true.
    ("idle_legitimacy", OWNED_TASK_SUBMITTED_AFTER): {
        "gate": {"likely-done-submit-now": 1.0},
    },
    ("idle_legitimacy", OWNED_TASK_ROTTED_AFTER): {
        "gate": {"likely-stranded-escalate": 1.0},
    },
    ("idle_legitimacy", IDLE_WAIT_RESOLVED_AFTER): {
        "gate": {"legit-wait": 1.0},
    },
    # respawn_verdict (B37): what the wedged task did after the trip.
    (RESPAWN_VERDICT_PILOT, TASK_DELIVERED_AFTER): {
        "gate": {"spawn": 1.0},
    },
    (RESPAWN_VERDICT_PILOT, TASK_CANCELLED_AFTER): {
        "gate": {"kill-task": 1.0},
    },
    (RESPAWN_VERDICT_PILOT, TASK_STALLED_PAST_WINDOW): {
        "gate": {"hold-task-for-human": 1.0},
    },
    # submit_now_confidence (B38, 3-level score): a QA bounce after a
    # confident submit grades the row low (soft gold, not a pole); clean
    # delivery grades it at the top.
    ("submit_now_confidence", QA_PASSED_AFTER): {
        "gate": {"2": 1.0},
    },
    ("submit_now_confidence", PLAN_BOUNCED_AFTER): {
        "gate": {"0": 0.7, "1": 0.3},
    },
    # pm_closure_confidence (B42, 3-level score): same axis as submit_now
    # one hop later in the lifecycle.
    ("pm_closure_confidence", QA_PASSED_AFTER): {
        "gate": {"2": 1.0},
    },
    ("pm_closure_confidence", PLAN_BOUNCED_AFTER): {
        "gate": {"0": 0.7, "1": 0.3},
    },
    # plan_quality (B1, 3-level score): a bounced plan was inadequate;
    # a plan that shipped unreplanned was at least adequate (soft between
    # adequate and strong); a stalled one was likely inadequate.
    ("plan_quality", PLAN_BOUNCED_AFTER): {
        "gate": {"0": 1.0},
    },
    ("plan_quality", QA_PASSED_AFTER): {
        "gate": {"1": 0.5, "2": 0.5},
    },
    ("plan_quality", PLAN_STALLED_PAST_WINDOW): {
        "gate": {"0": 0.7, "1": 0.3},
    },
    # preflight_diff (6.4): graded ONLY when QA fully passed - every
    # criterion's "plausibly addresses" claim held and the hygiene claim
    # was false. Bounced rows keep their inputs (per-criterion truth needs
    # finding-to-criterion matching, Wave 2).
    ("preflight_diff", QA_PASSED_AFTER): {
        **{f"criterion_{i}": {"true": 1.0, "false": 0.0} for i in range(6)},
        "hygiene": {"true": 0.0, "false": 1.0},
    },
    # parking (6.2): the provider-probe lift timing and re-limit evidence
    # prove which handling was true. A lift inside the short-retry window
    # proves a short retry would have sufficed; a lift after the standard
    # cooldown proves the standard park was right; a re-limit proves the
    # repeated-failure escalation was warranted.
    ("parking", LIMIT_LIFTED_QUICKLY): {
        "gate": {"retry_soon": 1.0},
    },
    ("parking", LIMIT_LIFTED_AFTER_COOLDOWN): {
        "gate": {"park_standard": 1.0},
    },
    ("parking", RE_LIMITED_ON_RESUME): {
        "gate": {"escalate": 1.0},
    },
    # triage_failure (6.5): the test's own later history proves the cause.
    # The same test failing again on tasks whose diffs share no files with
    # this one proves flake; this task delivering and the test never
    # failing again proves the regression (and its fix).
    ("triage_failure", FLAKE_CONFIRMED_LATER): {
        "gate": {"flaky": 1.0},
    },
    ("triage_failure", REGRESSION_CONFIRMED_LATER): {
        "gate": {"my_regression": 1.0},
    },
    # second_review_eligibility (B9, noul "high-stakes"): a post-gate
    # bounce proves the second review was warranted; a clean delivery
    # proves it was not (documented noise: a clean pass could also mean
    # the first review was simply good).
    ("second_review_eligibility", QA_PASSED_AFTER): {
        "gate": {"true": 0.0, "false": 1.0},
    },
    ("second_review_eligibility", PLAN_BOUNCED_AFTER): {
        "gate": {"true": 1.0, "false": 0.0},
    },
    # assembled_coherence (B43, 3-level score): bounced from the gate
    # leans incoherent; passed the gate leans coherent (soft golds).
    ("assembled_coherence", QA_PASSED_AFTER): {
        "gate": {"1": 0.5, "2": 0.5},
    },
    ("assembled_coherence", PLAN_BOUNCED_AFTER): {
        "gate": {"0": 0.7, "1": 0.3},
    },
}

# Per-question fate golds for BATCHED pilots whose questions each carry
# their own truth (one row-level outcome slug cannot express them). The
# labeler stamps ``question_outcomes`` = {question_key: fate} and the
# exporter resolves each fate through this map. A fate mapped to None is
# real but unusable for that pilot's training (excluded per question).
QUESTION_FATE_GOLD: dict[str, dict[str, dict[str, float] | None]] = {
    # findings_mapping (B31): per-finding noul "this diff plausibly
    # addresses finding F". The finding's ledger fate is the truth.
    "findings_mapping": {
        "resolved": {"true": 1.0, "false": 0.0},
        "re_raised": {"true": 0.0, "false": 1.0},
        # A waived finding says nothing about whether the diff addressed
        # it: exclusion, never a guessed gold.
        "waived": None,
    },
}

# (pilot, outcome slug) rows that must NEVER become training examples:
# the outcome is real but the gold it implies is confounded (e.g. the
# breach "cleared" because the engine's own fix task is open against it,
# so neither "transient" nor "defect" is the honest label).
UNUSABLE_OUTCOMES: frozenset[tuple[str, str]] = frozenset(
    {
        (SELF_HEAL_PILOT, SUPERSEDED_BY_FIX_TASK),
        # A parking row with no lift evidence before the rot horizon is
        # ambiguous (the probe path may simply never have stamped), so it
        # is retired rather than graded escalate.
        ("parking", STALE_UNRESOLVED),
    }
)


def gold_for(pilot: str, outcome: str) -> Gold | None:
    """The gold for one labeled row, or ``None`` when the slug is unknown
    for this pilot or marked unusable (either way: skip, never guess)."""
    key = (pilot, outcome)
    if key in UNUSABLE_OUTCOMES:
        return None
    return OUTCOME_GOLD.get(key)


def question_fate_gold(
    pilot: str, fate: str
) -> dict[str, float] | None:
    """One question's fate resolved to gold probabilities, or ``None``
    when the pilot has no fate map, the fate is unknown, or the fate is
    explicitly unusable for training."""
    fate_map = QUESTION_FATE_GOLD.get(pilot)
    if not fate_map:
        return None
    return fate_map.get(fate)


def usable_slugs() -> dict[tuple[str, str], Gold]:
    """Registry view for tooling/tests: every slug that yields gold."""
    return dict(OUTCOME_GOLD)


def validate_gold_shape(qtype: str, probabilities: dict[str, float]) -> bool:
    """True when one gold probability map fits its question type: noul
    sums over true/false, choice over named options, score over legend
    indexes. Malformed gold is an exporter bug, so the exporter asserts
    through this rather than emitting a corrupt training row."""
    if not probabilities:
        return False
    total = sum(probabilities.values())
    if abs(total - 1.0) > _GOLD_SUM_TOLERANCE:
        return False
    if qtype == "noul":
        return set(probabilities) <= {"true", "false"}
    if qtype == "score":
        return all(k.isdigit() for k in probabilities)
    return True  # choice: any non-empty option-keyed map summing to 1


def example_gold(gold: Gold, result_questions: dict[str, Any]) -> Gold:
    """Restrict a row's gold to the question keys the row actually asked,
    so registry entries can never inject keys a row never had."""
    return {key: g for key, g in gold.items() if key in result_questions}
