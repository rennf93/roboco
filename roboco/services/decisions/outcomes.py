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

# Outcome slugs, grouped by the pilot that produces them. A slug is part
# of the training contract: renaming one silently orphans every row
# already labeled with it, so both sides import these constants.
CLEARED_AFTER_GATE = "cleared_after_gate"
STILL_FAILING_AFTER_WINDOW = "still_failing_after_window"
SUPERSEDED_BY_FIX_TASK = "superseded_by_fix_task"

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
}

# (pilot, outcome slug) rows that must NEVER become training examples:
# the outcome is real but the gold it implies is confounded (e.g. the
# breach "cleared" because the engine's own fix task is open against it,
# so neither "transient" nor "defect" is the honest label).
UNUSABLE_OUTCOMES: frozenset[tuple[str, str]] = frozenset(
    {(SELF_HEAL_PILOT, SUPERSEDED_BY_FIX_TASK)}
)


def gold_for(pilot: str, outcome: str) -> Gold | None:
    """The gold for one labeled row, or ``None`` when the slug is unknown
    for this pilot or marked unusable (either way: skip, never guess)."""
    key = (pilot, outcome)
    if key in UNUSABLE_OUTCOMES:
        return None
    return OUTCOME_GOLD.get(key)


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
