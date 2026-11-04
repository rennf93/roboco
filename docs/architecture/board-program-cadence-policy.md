# Board Program cadence-slowing policy and CEO cadence review

A Board Program whose approval rate does not justify its cycle cost is run less often. The CEO can see every program's numbers and reverse any slowdown. The policy consumes the per-program metric from `roboco/services/board_program_cadence.py` (`cost_per_cycle_usd`, `approval_rate`, `items_proposed`, `items_approved`, `cycles_counted`).

## The rule

Implemented as a pure function, `decide_slowdown`, in `roboco/foundation/policy/board_program_cadence.py`. A program is slowed only when all three hold:

1. **Sample**: `items_proposed >= board_cadence_min_proposals`. Below the minimum a program is never slowed (n-1 is not slowed, n is eligible).
2. **Cost**: `cost_per_cycle_usd >= board_cadence_cost_threshold_usd`. The boundary is inclusive. A missing cost (`None`) is never slowed.
3. **Unjustified**: cost per approved proposal is at or above the threshold. Cost per approved proposal = `cost_per_cycle / (items_approved / cycles_counted)`. A program with zero approvals has an infinite cost per approved proposal, so it is slowed. The cost is justified while this figure stays strictly below the threshold.

Example with the $50 default: $60 per cycle, 2 cycles, 4 approved gives 2 approved per cycle, so $30 per approved proposal. That is below $50, so the program is not slowed. With 1 approved per cycle it is $60 per approved proposal, so it is slowed.

## Effective interval

`effective_interval_seconds(base, slowed=, multiplier=)` returns `base` when not slowed, otherwise `max(base, int(base * multiplier))`. It can only delay, never accelerate, even if the multiplier is set below 1.0 (the setting itself is validated `>= 1.0`).

The base interval comes from `base_interval_seconds(key)` in `roboco/services/board_program_cadence_policy.py`: the operator override (`roadmap_interval_seconds` for `roadmap`, `x_feature_spotlight_interval_seconds` for `x_feature`), else the program's registry `default_interval_seconds`. This helper replaces the removed `_interval_override` in `board_programs.py`.

`BoardProgramEngine._run_due_one` now passes `effective_interval(key, base)` as `interval_override` to `program_due`. The decisions-due-early path is untouched. The lookup runs inside a savepoint (`session.begin_nested()`) and **fails open**: any error in the metrics lookup is logged and the base interval is used, so a metrics failure never stalls a program.

## Settings

All in `roboco/config.py`, none hard-coded in the policy:

| Setting | Default | Constraint | Meaning |
|---|---|---|---|
| `board_cadence_cost_threshold_usd` | `50.0` | `>= 0` | Cycle cost at or above which a program can be slowed; also the per-approved-proposal limit. |
| `board_cadence_min_proposals` | `5` | `>= 1` | Proposals needed before a program can ever be slowed. |
| `board_cadence_slowdown_multiplier` | `2.0` | `>= 1.0` | Factor a slowed program's interval is stretched by. |

## CEO review and reversal

Both routes live in `roboco/api/routes/board_programs.py`, are CEO-only, and delegate to `BoardProgramCadencePolicyService`.

- `GET /board-programs/cadence-review` returns a `ProgramCadenceReview` for every program (`roboco/models/board_program_cadence_review.py`): `program_key`, `cost_per_cycle_usd`, `approval_rate`, `sample_size`, `items_approved`, `slowed`, `slowdown_disabled`, `original_interval_seconds`, `effective_interval_seconds`, `reason`, and the triggering numbers `cost_threshold_usd`, `min_proposals`, `slowdown_multiplier`, `cost_per_approved_usd` (`null` when infinite or not computed).
- `POST /board-programs/{key}/cadence-slowdown?disabled=true|false` reverses (`true`) or restores (`false`) a program's slowdown and returns the refreshed review. Unknown keys return 404.

Reversal is stored in the settings store under `board_program.{key}.cadence_slowdown_disabled` (validated as a boolean for every key in `PROGRAMS`). When set, `effective_interval` returns the base interval and the review shows `slowed=false`, `slowdown_disabled=true`.

## Tests

- `tests/unit/foundation/policy/test_board_program_cadence.py`: sample boundary (n-1 / n), inclusive threshold, configurable threshold, justified vs unjustified approval, never-accelerates.
- `tests/unit/services/test_board_program_cadence_policy.py`: review lists every program with its numbers, reversal, and the `_run_due_one` delay behaviour.
