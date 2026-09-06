# Shipped-work digest helper

`roboco/utils/shipped_work_digest.py` assembles a shipped-this-week digest (completed tasks plus the CHANGELOG Unreleased section) into a single string that board-program exploration prompts (roadmap/Printer, Pest Control, Spackle) inject so the explorer sees what the fleet just shipped and doesn't re-propose already-shipped work. It was extracted from `megaphone_engine.py`'s original assembly into a shared helper so every board program gets the same view.

## Lazy-import constraint

The module lives in `roboco/utils/`, which the architectural standard requires to stay clear of service-module imports at import time. The module docstring states this explicitly. Both service imports — `roboco.services.workspace` and `roboco.services.release_readiness` — are therefore **lazy**: they live inside the `_unreleased_changelog` function body, not at module level, each marked `# noqa: PLC0415` (the ruff rule that would otherwise flag a non-top-level import). A future change that adds a service import at module level violates the standard and contradicts the docstring — keep service imports lazy inside the function that uses them.

## Changelog-failure warning log

`_unreleased_changelog` catches every exception and returns an empty string — it never raises (the caller renders the empty case explicitly). But it does **emit a warning log** before degrading:

```python
logger = get_logger(__name__)
# ...
except Exception as exc:
    logger.warning(
        "shipped-work-digest: changelog read failed (best-effort)",
        error=str(exc),
    )
    return ""
```

This restores the observability `megaphone_engine.py`'s original implementation had — a swallowed exception with no log is a silent failure that's hard to diagnose. The warning uses structlog's structured `error=` field, matching the `roboco/utils/crypto.py` logger pattern. The test `test_changelog_exception_emits_warning_log` (in `tests/unit/test_shipped_work_digest.py`) pins this behavior by monkeypatching the module-level `logger` with a `MagicMock` and asserting `fake_logger.warning` was called — deterministic regardless of structlog's processor-chain state, unlike `capture_logs`/`caplog` — if the warning stops firing, the test fails.

## Degradation behavior

When the changelog read fails (file absent, section blank, clone error, etc.), `_unreleased_changelog` returns `""` and the digest renders `(not available this cycle)` in place of the Unreleased section. The completed-task section is unaffected — it comes from a DB query, not the changelog. The digest never raises; every failure mode degrades to a renderable string.