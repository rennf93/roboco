# Sonnet-5 Promo Date-Bomb Test Repair — Approvals Branch (PR #996)

## Summary

PR #994 (phone Approvals tab: the four board-program decision queues) bounced purely on CI: the "Python quality gate" job was red on its head commit. The PR's own diff carries no Python and had passed manual review on every criterion — the red came from a **date bomb** the assembled branch happened to carry: `TestSonnet5PromoTier` in `tests/unit/billing/test_pricing.py` pins the claude-sonnet-5 promotional rates (2.01/10.05/0.201/0.5025 per million) with no date control, while `roboco/billing/pricing.py` gates those rates behind `_SONNET5_PROMO_END = date(2026, 8, 31)`. The branch was assembled on Aug 31 while the promo was live; from 2026-09-01 `date.today()` exceeds the window, `_lookup_prices` returns list rates (3.00/15.00/0.30/0.75), and all 7 promo assertions fail deterministically (`calculate_cost("claude-sonnet-5", 1_000_000 input)` returns 3.0 vs expected 2.01).

## Fix

Tests-only, one file: `tests/unit/billing/test_pricing.py` (commit 8294e1da). `roboco/billing/pricing.py` and the approvals-tab diff are untouched.

1. **Promo branch frozen open.** `TestSonnet5PromoTier` gains an autouse `_promo_window_open` fixture monkeypatching `p._SONNET5_PROMO_END` to `_PROMO_FAR_FUTURE_END = date(2099, 12, 31)`, so the 7 promo-rate assertions hold on any run date.
2. **List branch covered.** New `TestSonnet5ListTier` class (per-rate, all-token-types, and parity-with-sonnet-4.6 tests) pins the end to `_PROMO_PAST_END = date(2026, 8, 30)` — a past date the real `date.today()` always exceeds — and asserts the list rates 3.00/15.00/0.30/0.75. Both branches of the pricing gate stay covered forever.

**Why patch the constant, not the clock:** `_sonnet5_prices` reads `date.today()` against `_SONNET5_PROMO_END` directly, so patching the END constant exercises the *real* gate expression with only the window pinned, and avoids defining a fake-date shim in every test. This differs from the sibling repair on the Cost-Tiered branch (PR #998), which monkeypatches `p.date` with a frozen `today()` — same pattern family, both deterministic; main-pm consolidates whichever wins when the branches assemble.

## Verification

- Red-before: all 7 `TestSonnet5PromoTier` tests failed in a fresh worktree venv (rebuilt via `make sync` from the branch `uv.lock`), e.g. `calculate_cost("claude-sonnet-5", 1_000_000)` = 3.00 vs expected 2.01.
- Green-after: `pytest tests/unit/billing/test_pricing.py` = 96 passed on 2026-09-01, a date already past the real promo window; `make gate` (lint + typecheck) clean. QA independently re-ran the suite on 2026-09-01 and confirmed 96/96 with no monkeypatch leakage between classes.
- Postgres/Redis-dependent test failures and the local coverage shortfall under a service-less local run are known artifacts of missing local services; they pass under the CI job's service containers and are not regressions.

## Pattern note

Any test asserting a date-gated behavior must pin the gate on both edges — an "active" window pinned to a far-future end, a "closed" window pinned to a past end — or the suite date-bombs on a calendar boundary with zero code change, exactly as this branch did the night the promo expired.