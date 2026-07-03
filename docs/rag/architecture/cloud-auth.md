# Cloud Auth

## What It Is

A cookie-session login for the **single human CEO user**, built on FastAPI Users, that lets the panel/API be safely exposed beyond localhost. Implemented in `roboco/api/auth/` (`backend.py`, `session.py`, `manager.py`, `seed.py`, `routes.py`). It is **not a panel feature flag** — it is env-only, deliberately absent from `roboco/services/settings.py`'s `FEATURE_FLAGS` tuple, so it can't be toggled on for a deployment that isn't already behind TLS.

## Enable/Disable

| Variable | Default | Effect |
|----------|---------|--------|
| `ROBOCO_CLOUD_AUTH_ENABLED` | `false` | Master switch. Off: `get_agent_context` (the single dependency every API route resolves its caller through) behaves byte-for-byte as today's header-trust. On: no registration router is mounted — exactly one user, seeded from `ROBOCO_CLOUD_AUTH_EMAIL` / `_PASSWORD`. |
| `ROBOCO_CLOUD_AUTH_EMAIL` | (unset) | Email for the single seeded user. |
| `ROBOCO_CLOUD_AUTH_PASSWORD` | (unset) | Password for the single seeded user. Hashed at startup (bcrypt via `PasswordHelper`), never stored in plain text. |
| `ROBOCO_CLOUD_AUTH_SECRET` | (unset) | JWT signing secret for the session cookie. **Fails loud at startup** — `Settings._validate_cloud_auth` raises before the app boots — if `cloud_auth_enabled=true` without this set. |
| `ROBOCO_CLOUD_AUTH_COOKIE_MAX_AGE` | `2592000` (30 days) | Sliding session lifetime in seconds. |

## Single seeded user, no registration

`ensure_seed_user_startup()` runs at lifespan startup: if `cloud_auth_enabled` and both email/password are set, it upserts **exactly one** `UserTable` row, looked up by primary key (not email) so changing the email renames the existing row instead of creating a second one. There is no registration route — the only way to get a login is this seed. If cloud auth is on but email/password are unset, startup logs a warning and every login attempt is rejected (fail-safe, not fail-open).

## Sliding session, password-fingerprint JWT

The session cookie (`roboco_session`, `SESSION_COOKIE_NAME` in `backend.py`) is **secure-only** (`cookie_secure=True`), `httponly`, `samesite=lax` — **arm this only behind TLS**, or browsers silently refuse to send the cookie and login appears to fail with no clear error.

Every authenticated request re-mints and re-sets the cookie (`_slide_session_cookie` in `roboco/api/deps.py`), so an in-use session never lapses — only genuine inactivity past `cloud_auth_cookie_max_age` logs out. The JWT itself binds a **password fingerprint** (`_password_fingerprint` — first 16 hex chars of a SHA-256 of the current `hashed_password`) as a `pwd_fp` claim; `_SlidingSessionStrategy.read_token` rejects any token whose fingerprint no longer matches. A JWT is otherwise stateless and can't be revoked by user id alone — this makes rotating the seeded password (env change + restart) invalidate every previously-issued cookie.

## Dual-path `get_agent_context`

`roboco/api/deps.py`'s `get_agent_context` is the single dependency every route resolves its caller through, and it branches on the flag:

- **Off** (default): delegates straight to `_header_trust_agent_context` — the historical `X-Agent-ID` / `X-Agent-Role` / `X-Agent-Team` / `X-Agent-Token` header path, unchanged.
- **On**: `_cloud_auth_agent_context` enforces dual-path. A caller presenting a **valid HMAC `X-Agent-Token`** (the agent fleet, and the orchestrator's `system` self-PATCH) is accepted exactly like today — delegated to the header-trust path once the token verifies. Without a valid token, any agent-role claim in the headers is treated as an unauthenticated spoof and rejected **regardless of role** — this closes the LAN header-spoof hole on the published API port for every privileged role, not just `ceo` (the pre-cloud-auth surface only ever worried about spoofing the CEO). The sole remaining legitimate caller without a token is the human CEO with a **valid session cookie**, resolved via `resolve_session_user`.

Agent HMAC auth and the orchestrator's `system` self-PATCH are untouched in both modes — cloud auth only closes the *unauthenticated header* path, never the *signed token* path.

## Panel wiring

`panel/src/proxy.ts` (the Next.js middleware entry) probes `GET /api/auth/status` on every non-API, non-static request; if `cloud_auth_enabled` is true and the `roboco_session` cookie is absent, it redirects to `/login`. The probe fails open (treats a slow/unreachable backend as "cloud auth off") within a 1.5s timeout — a stuck backend must never turn into a stuck redirect loop.

## Related

- `docs/rag/architecture/config-reference.md` — full env var table
- `docs/rag/architecture/db-network-isolation.md` — a separate, unrelated hardening (network topology, not auth) that ships alongside cloud auth on the NAS composes
