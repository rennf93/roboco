# HTTP Security Guard

## What It Is

RoboCo's HTTP request layer is protected by `fastapi-guard` (v7.2.1), implemented in `roboco/security.py` and wired into the app in `roboco/api/app.py`'s `create_app`.

## Enable/Disable

| Variable | Default | Effect |
|----------|---------|--------|
| `ROBOCO_GUARD_ENABLED` | `false` | Master switch. Off = completely inert — no middleware is mounted, the request path is entirely unchanged, and nothing is logged or blocked. |
| `ROBOCO_GUARD_PASSIVE_MODE` | see below | When the guard is enabled, controls whether it blocks matching requests or only logs them. |

As of 2026-07-01 the guard is built on the `feature/fastapi-guard-hardening` branch, gated off by default, and wherever it is enabled at all it is running in passive/log-only mode — so no agent request is currently being blocked by it anywhere.

## When Armed

With `ROBOCO_GUARD_ENABLED=true`, a `SecurityMiddleware` sits outermost in the middleware stack, and per-route decorators add rate limits, request-size caps, content-type filters, a signature-based WAF (detects SQL injection, XSS, path traversal, and suspicious URL patterns), security response headers, cloud-provider/honeypot checks, and an emergency lockdown switch.

On top of those generic checks, three RoboCo-specific custom validators run against request bodies:

| Validator | Blocks |
|-----------|--------|
| Prompt-injection detection | Bodies attempting to inject instructions |
| Secret-exfil detection | Bodies carrying literal credential-shaped strings (e.g. `sk-ant-...`, `ghp_...`, postgres connection URLs) or phrasing like "reveal your api keys" |
| Internal-SSRF detection | Fetch-type bodies targeting internal/metadata hosts (e.g. `169.254.169.254`, `roboco-*` internal service hostnames) |

## Enforcement Posture

`ROBOCO_GUARD_PASSIVE_MODE` decides what happens on a match: `true` (passive) detects and logs only, and never blocks a request — this is how the NAS production deploy is armed today. `false` (enforce) actually blocks the matching request.

A blocked request gets a generic `400` or `403` response — no rule or signature detail is returned, so the response body can't be used to fingerprint what tripped the guard.

## WAF Calibration for Agent Traffic

Agent traffic legitimately carries code, SQL, diffs, file paths, HTML snippets, and URLs — for example inside `note` / `commit` / `say` bodies. To avoid false positives, the free-text body fields on those routes are excluded from WAF signature scanning via `excluded_detection_body_fields` in `build_security_config`, so normal code/SQL/diff/HTML payloads from agents are not flagged by the WAF layer.

The three custom validators above are not covered by that exclusion — they scan those same bodies regardless of the WAF exclusion. See `docs/rag/troubleshooting/blocked-http-requests.md` for what this means in practice and what not to put in a request body.
