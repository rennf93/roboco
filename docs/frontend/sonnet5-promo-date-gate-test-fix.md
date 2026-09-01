# Sonnet-5 Promo Date-Gate Test Fix (PR #998)

## Summary

The Python quality gate on the Cost-Tiered PR head went red with 7 test failures in `tests/unit/billing/test_pricing.py` (`TestSonnet5PromoTier`). The cause was not the PR's diff (which touches only the panel) — it was a **date-gate time bomb** in the test suite: the promo tests asserted promotional rates unconditionally, and the promotional pricing they pin auto-reverts to list rates after `2026-08-31`. The failing CI run straddled that UTC midnight (started 2026-08-31T23:54:39Z), so the assertions failed the moment the promo window closed.

## Root cause

`roboco/billing/pricing.py` carries a deliberate promo: `claude-sonnet-5` is priced at 33% off Sonnet 4.6 through `_SONNET5_PROMO_END = 2026-08-31`, with an automatic revert to list rates after that date (`date.today() <= _SONNET5_PROMO_END`). The revert is product behavior by design, not a bug.

`TestSonnet5PromoTier` (7 tests) pinned the promo rates with no date control. Under the real clock on 2026-09-01, `calculate_cost("claude-sonnet-5", tokens_input=1_000_000)` returned `3.0` (list) instead of `2.01` (promo), failing every rate assertion with `assert 0.99 < 0.0001`.

## Fix

The failing stage was `make quality`'s pytest step (CI annotation: "Process completed with exit code 2"; the preceding alembic-upgrade step passed). The fix is test-only, one file:

- `tests/unit/billing/test_pricing.py` — `TestSonnet5PromoTier` gains an `autouse` `_promo_window` fixture that monkeypatches the module's `date` symbol with a static `today()` returning `2026-08-31` (the last promo day; the gate is inclusive). All 7 rate assertions now run deterministically inside the frozen promo window, forever.
- No product code changed. No `# noqa` / `# type: ignore` suppressions were added. `panel/` and `roboco/services/llm.py` are untouched.

The gate's on/off behavior itself remains covered by the two pre-existing module-level date-gate tests, `test_sonnet5_promo_active_on_or_before_2026_08_31` and `test_sonnet5_reverts_to_list_rate_after_2026_08_31`.

## Pattern note: date-gated logic needs a frozen clock in tests

Any test asserting behavior gated on `date.today()` must pin the clock, or it becomes a time bomb that fails on a future date with no code change at all. The convention in this file: monkeypatch the module-level `date` imported by `roboco/billing/pricing.py` (`monkeypatch.setattr(p, "date", _D)` with a static `today()`), as the promo-rate tests and both expiry tests do. Fresh-lockfile verification (CPython 3.13.15, guard-core 3.15.0, mcp 2.1.1 via `uv sync --extra dev`) confirmed the repair is green: 90/90 pricing tests pass, `mypy roboco/ tests/` clean, ruff clean — and the earlier suspicion of fresh-env mypy errors in `roboco/security.py` / `roboco/mcp/*` did **not** reproduce under the current lock.