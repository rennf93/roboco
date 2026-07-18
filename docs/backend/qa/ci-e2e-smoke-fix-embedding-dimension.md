# CI Fix: E2E Smoke Test Embedding Dimension Mismatch

**Round 3 — Resolved**

## Root Cause

The e2e smoke test `test_data_integrity.py::test_c3_deleted_journal_unindexed` was seeding a 4-dimensional placeholder vector into `chunks_journals`, but the e2e stack's app lifespan eagerly initializes every OptimalService plugin (including JOURNALS) at startup, which creates that table with the **real configured embedding dimension** (1024, from `settings.embedding_dimensions`) before the test ever runs. The insert failed with:

```
asyncpg.exceptions.DataError: expected 1024 dimensions, not 4
```

The test's own comment ("the table is created fresh per run") was stale: the app doesn't create it fresh on each test, it eagerly creates it once at startup.

## Solution Applied

Changed `_SMOKE_DIM` from a hardcoded constant to derive from the real runtime configuration:

```python
# Before
_SMOKE_DIM = 4  # tiny embedding dim — the table is created fresh per run

# After
_SMOKE_DIM = settings.embedding_dimensions
```

Now the seeded placeholder vector always matches the table's actual column width, regardless of the configured embedding dimension.

## Impact

- **Scope:** Test infrastructure only (e2e smoke test)
- **Risk:** Minimal — single constant reference change in the test module
- **Behavior:** No change to the code under test; the e2e module itself is unaffected
- **Verification:** Provisioned real postgres+redis sandbox matching `.github/workflows/e2e-smoke.yml`, ran full `make e2e-smoke` suite (51 tests), all passed

## Pattern

For future e2e smoke tests that seed placeholder data into a schema initialized at app startup, source placeholder dimensions from the runtime settings, not hardcoded constants:

```python
from roboco.config import settings

# Correct: placeholder always matches runtime schema
placeholder_dim = settings.embedding_dimensions
```

Do not assume the schema is created fresh per test — eager initialization hooks may create it once at app startup, before any individual test runs.

## Session Summary

Round 3 comprehensively diagnosed every failing CI stage:
- All 14 `make quality` stages were already clean
- The sole real failure was this e2e-smoke embedding-dimension bug
- All code-level failures from rounds 1–2 (mypy regression, SDK URL isolation) were already fixed
- This final fix clears both the Python quality gate and e2e lifecycle smoke checks for PR #566
