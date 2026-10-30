---
paths:
  - "roboco/api/auth/**"
  - "roboco/api/deps.py"
  - "roboco/api/websocket.py"
  - "roboco/api/routes/telegram.py"
  - "panel/src/proxy.ts"
  - "panel/src/app/(auth)/**"
  - "panel/src/lib/client.ts"
---

# Cloud auth via FastAPI Users

Migrated from the root CLAUDE.md so it loads only when the matching files are touched.

**Cloud auth via FastAPI Users (default-off).** Lets the panel/API be safely exposed beyond localhost without touching the CEO's local no-login flow while off. Gated by `ROBOCO_CLOUD_AUTH_ENABLED` (+ `ROBOCO_CLOUD_AUTH_EMAIL` / `_PASSWORD` / `_SECRET` / `_COOKIE_MAX_AGE`; `Settings` fails loud at startup if the flag is on with no secret). Off: `get_agent_context` (`roboco/api/deps.py`) and the WS `_require_panel_token` gate (`roboco/api/websocket.py`) are byte-for-byte unchanged (header-trust). On: header-trust is dead for humans - any agent-role claim (`ceo` OR a privileged `main_pm`/`cell_pm`/board role) with no valid HMAC token or session cookie is 401, closing the header-spoof hole on the host-published `:8000` port for every role, not just `ceo` (real agents always carry a signed token, so they're unaffected); the agent-fleet HMAC path (and the orchestrator's `system` self-PATCH) keeps working unmodified in both modes; a valid session cookie authenticates as the single seeded CEO user. New `users` table (migration 058, `UserTable` in `roboco/db/tables.py`) backs FastAPI Users' `SQLAlchemyUserDatabase`; no registration router - `roboco/api/auth/seed.py` idempotently upserts exactly one row from env at startup (by primary key, so an email change renames the row instead of duplicating it). `roboco/api/auth/backend.py` wires a **cookie** transport (httponly, secure, samesite=lax) + a `JWTStrategy` subclass that binds each token to a fingerprint of the current `hashed_password`, so rotating the seeded password invalidates every prior session. Session lifetime is **sliding**: every authenticated request through `get_agent_context` re-mints + re-sets the cookie (`_slide_session_cookie`), so an active session never expires - only genuine inactivity past `cloud_auth_cookie_max_age` (default 30 days) logs out. `GET /api/auth/status` is always mounted (public); `/api/auth/login` + `/api/auth/logout` mount only when armed (`roboco/api/auth/routes.py`, mirroring `apply_guard`'s conditional mount). A second route mints the identical cookie without a password: `POST /api/telegram/webapp-auth` (`roboco/api/routes/telegram.py`), mounted only when `telegram_miniapp_enabled` AND `cloud_auth_enabled` are both armed - see the Telegram bridge entry below. Panel: `(auth)/login/page.tsx` + `proxy.ts` (the Next 16 rename of `middleware.ts`; probes `/auth/status` over the docker-internal orchestrator URL, not through nginx, and fails open to "off" on any probe error/timeout) gate the `(dashboard)` group; `client.ts` adds `withCredentials` + a 401→`/login` redirect. nginx needs no changes (`/api/auth/*` rides the existing `/api/` proxy location) - but its own static `X-Agent-Token` injection (`ROBOCO_PANEL_AGENT_TOKEN`) is itself a valid HMAC credential that bypasses login when present, so a deployment arming cloud auth for real public exposure should leave that token unset (the two are alternative human-auth tiers, not layered).

