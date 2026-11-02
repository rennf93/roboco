# Shipped-work digest helper

`roboco/services/shipped_work_digest.py` assembles a shipped-this-week digest (completed tasks plus the CHANGELOG Unreleased section) into a single string that board-program exploration prompts (roadmap/Printer, Pest Control, Spackle) inject so the explorer sees what the fleet just shipped and doesn't re-propose already-shipped work. It was extracted from `megaphone_engine.py`'s original assembly into a shared helper so every board program gets the same view.

## Layer

The module is a service: it queries the database and calls `get_workspace_service(session).ensure_read_clone(...)`, which can clone a repository. It first shipped in `roboco/utils/` (declared "shared, side-effect-free helpers") with its table and service imports deferred into the function bodies, and moved to `roboco/services/` in 2026-10. Its imports are now ordinary module-level imports, and `roboco/utils/__init__.py` no longer re-exports it.

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

This restores the observability `megaphone_engine.py`'s original implementation had — a swallowed exception with no log is a silent failure that's hard to diagnose. The warning uses structlog's structured `error=` field, matching the `roboco/utils/crypto.py` logger pattern. The test `test_changelog_exception_emits_warning_log` (in `tests/unit/services/test_shipped_work_digest.py`) pins this behavior by monkeypatching the module-level `logger` with a `MagicMock` and asserting `fake_logger.warning` was called — deterministic regardless of structlog's processor-chain state, unlike `capture_logs`/`caplog` — if the warning stops firing, the test fails.

## Degradation behavior

When the changelog read fails (file absent, section blank, clone error, etc.), `_unreleased_changelog` returns `""` and the digest renders `(not available this cycle)` in place of the Unreleased section. The completed-task section is unaffected — it comes from a DB query, not the changelog. The digest never raises; every failure mode degrades to a renderable string.