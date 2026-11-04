# Removal of dead `no_helpers_in_routers` convention rule

Task 685a33aa, PR #1500 (resolves PR-gate finding F-1c7648ca).

## What changed

`.roboco/conventions.yml` only (comment/config, no code):

- Deleted the `no_helpers_in_routers` entry (level `warn`) and its two-line comment ("Violations remain ...").
- Reworded the posture comment (top of file) and the closing "inherits BLOCK" comment so they refer only to `no_helpers_in_routes` / routes.
- `no_helpers_in_routes` is unchanged and stays at `warn`.

A `yaml.safe_load` before/after comparison showed `no_helpers_in_routers` was the only rule that disappeared.

## Why

Rule names are built as `no_{kind}s_in_{leaf}` (`roboco/conventions/placement.py:78`, `roboco/conventions/scan.py:157`). roboco-api has no `routers/` directory and no module with a `routers` leaf, so the rule could never fire here, and the comment claiming violations remained was false for this file.

## Where the 22 `no_helpers_in_routers` findings come from

Not from this repo. The scan heuristic (`scan.py:50`) lists `routers` as a route-module name, so any project that has a `routers/` directory gets that rule derived. The only references to the rule name inside roboco-api are unit-test fixtures (`tests/unit/conventions/test_scan.py`, `tests/unit/foundation/policy/conventions/*`). The 22 findings therefore belong to another project's derived conventions or to stale conventions hotspot-ledger data. The developer had no ledger access to confirm which; treat the exact source as unconfirmed.

## Impact

None at runtime. Route-helper debt in roboco-api remains tracked by `no_helpers_in_routes` (warn); live counts live in the conventions hotspot ledger.
