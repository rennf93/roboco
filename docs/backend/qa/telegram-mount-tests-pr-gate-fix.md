# PR Gate Fix - Telegram Webapp-Auth Conditional-Mount Tests

**Finding F-9e193527 (major) - Resolved (PR #864)**

## Root Cause
`tests/integration/test_telegram_webapp_auth.py` had three conditional-mount tests - `test_mount_skipped_when_miniapp_flag_off`, `test_mount_skipped_when_cloud_auth_off`, `test_mount_included_when_both_armed` - that called a test-local helper, `_mount_miniapp_auth`, which duplicated the inline conditional-mount logic that actually lives in `roboco/api/app.py` (lines 560-571). The helper's own docstring admitted it was a "test-local mirror of `roboco.api.app`'s inline conditional mount." Because none of the three tests ever constructed `create_app()` with `telegram_miniapp_enabled` / `cloud_auth_enabled` toggled, the real mount gate in `app.py` had zero coverage - a regression there would have passed CI silently.

## Solution Applied
Repointed all three tests at the real `roboco.api.app.create_app()` factory, monkeypatching `settings.telegram_miniapp_enabled` / `settings.cloud_auth_enabled` instead of hand-rolling a mirror, following the exact pattern already established in `tests/unit/api/test_app.py` and `tests/unit/runtime/test_rate_limit_sweep.py`:

- Added a module-local `_registered_paths(app)` helper (mirroring `test_app.py`'s helper of the same name) that reads routes from `app.openapi()`'s path table plus each included router's prefix, since FastAPI 0.137+ wraps sub-routers in an `_IncludedRouter` with no `.path` attribute and doesn't flatten into `app.routes`.
- `test_mount_skipped_when_miniapp_flag_off` / `test_mount_skipped_when_cloud_auth_off` each monkeypatch one flag off (the sibling flag stays on via the module's autouse `_armed_settings` fixture) and assert `/api/telegram/webapp-auth` is absent from `_registered_paths(app)`.
- `test_mount_included_when_both_armed` calls `create_app()` with both flags already on (from the autouse fixture), asserts the route is present, and additionally asserts `LoginRateLimiter` is installed as middleware for that path - checking the union of every `LoginRateLimiter` instance's configured `paths` kwarg, since two separate instances are installed (one for `/api/auth/login`, one for the Telegram route).
- Deleted `_mount_miniapp_auth` and its now-unused `_post_unmounted_probe` live-request helper entirely; nothing references either anymore.

## Impact
- **Scope:** Test-only, single file (`tests/integration/test_telegram_webapp_auth.py`). No production code touched - `roboco/api/app.py`, `roboco/api/routes/telegram.py`, and `roboco/api/utils/telegram.py` were explicitly out of scope and unchanged.
- **Risk:** Minimal - the three tests were converted from exercising a hand-rolled mirror to exercising the real app factory; the exchange-flow tests in the same file (which use the `client`/`db_session` fixtures) were not touched.
- **Behavior:** No production behavior change. Test coverage change only: `app.py`'s conditional-mount gate (lines 560-571) is now exercised by real `create_app()` calls instead of having zero coverage.
- **Verification:** `make gate` green (ruff format/check + mypy clean); the 3 repointed mount tests pass locally. The 5 exchange-flow tests in the same file skip in sandboxes without a reachable Postgres - unrelated to this change and unaffected by it.

## Pattern
When a test needs to exercise a conditional code path that lives inside an app-wiring module (such as `app.py`'s `create_app()` factory), don't hand-roll a test-local mirror of that logic - it silently decouples the test from the code it's meant to protect, as `_mount_miniapp_auth`'s own docstring admitted. Instead, build the real app via the production factory and monkeypatch the settings that drive the branch, following whatever pattern the codebase has already established for that factory (here, `test_app.py`'s `_registered_paths` helper for route-presence assertions across FastAPI's `_IncludedRouter` wrapping, and `test_rate_limit_sweep.py` for the monkeypatch targets). Route-table assertions should read from `app.openapi()`'s path table plus router prefixes, not from `app.routes` directly, on FastAPI 0.137 and later.
