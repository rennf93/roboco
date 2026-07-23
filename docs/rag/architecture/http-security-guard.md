# HTTP Security Guard

## What It Is

RoboCo's HTTP request layer is protected by `fastapi-guard` (v7.2.1), implemented in `roboco/security.py` and wired into the app in `roboco/api/app.py`'s `create_app`.

## Enable/Disable

| Variable | Default | Effect |
|----------|---------|--------|
| `ROBOCO_GUARD_ENABLED` | `false` | Master switch. Off = completely inert — no middleware is mounted, the request path is entirely unchanged, and nothing is logged or blocked. |
| `ROBOCO_GUARD_PASSIVE_MODE` | see below | When the guard is enabled, controls whether it blocks matching requests or only logs them. |
| `ROBOCO_GUARD_EMERGENCY_WHITELIST` | `` (empty) | Comma-separated IPs/CIDRs always allowed through in an active `ROBOCO_GUARD_EMERGENCY` lockdown, in addition to loopback. Empty = loopback only. |
| `ROBOCO_GUARD_TRUSTED_CHAIN_PEERS` | `` (empty) | Comma-separated exact IP address(es) — never a CIDR range — trusted to appear as a recorded proxy hop inside `X-Forwarded-For` beyond loopback, e.g. the docker bridge gateway a host-proxied Tailscale Serve chain terminates behind, so the resolved client is the real tailnet/LAN peer instead of that hop's own address. Empty = only a loopback rightmost hop ever peels. |

As of 2026-07-19 the guard is gated off by default in config, but the NAS build compose arms it ON in ACTIVE enforcement (`ROBOCO_GUARD_PASSIVE_MODE=false`) — passive/log-only calibration came back clean, and the CEO approved the flip now that cloud auth + Tailscale are armed. A matching request on that deploy is actually blocked, not just logged. The registry compose still ships it fully off (see Enforcement Posture below).

## When Armed

With `ROBOCO_GUARD_ENABLED=true`, a `SecurityMiddleware` sits outermost in the middleware stack, and per-route decorators add rate limits, request-size caps, content-type filters, a signature-based WAF (detects SQL injection, XSS, path traversal, and suspicious URL patterns), security response headers, cloud-provider/honeypot checks, and an emergency lockdown switch.

On top of those generic checks, three RoboCo-specific custom validators run against request bodies:

| Validator | Blocks |
|-----------|--------|
| Prompt-injection detection | Bodies attempting to inject instructions |
| Secret-exfil detection | Bodies carrying literal credential-shaped strings (e.g. `sk-ant-...`, `ghp_...`, postgres connection URLs) or phrasing like "reveal your api keys" |
| Internal-SSRF detection | Fetch-type bodies targeting internal/metadata hosts (e.g. `169.254.169.254`, `roboco-*` internal service hostnames) |

## Enforcement Posture

`ROBOCO_GUARD_PASSIVE_MODE` decides what happens on a match: `true` (passive) detects and logs only, and never blocks a request. `false` (enforce) actually blocks the matching request — this is how the NAS build compose is armed today (its default flipped from `true` to `false` once passive-mode calibration reviewed clean). The registry compose omits the guard trio entirely, leaving a fresh third-party deploy on the safe config default (guard off).

A blocked request gets a generic `400` or `403` response — no rule or signature detail is returned, so the response body can't be used to fingerprint what tripped the guard.

## WAF Calibration for Agent Traffic

Agent traffic legitimately carries code, SQL, diffs, file paths, HTML snippets, and URLs — for example inside `note` / `commit` / `dm` bodies. To avoid false positives, the free-text body fields on those routes are excluded from WAF signature scanning via `excluded_detection_body_fields` in `build_security_config`, so normal code/SQL/diff/HTML payloads from agents are not flagged by the WAF layer.

The three custom validators above are not covered by that exclusion — they scan those same bodies regardless of the WAF exclusion. See `docs/rag/troubleshooting/blocked-http-requests.md` for what this means in practice and what not to put in a request body.

## Scanner Auto-Ban

A separate layer targets automated scanners (not agents — agents run on Docker-internal IPs). Repeated probes to scanner fingerprints on `/api` paths (`recon`/`sensitive_file`/`cms_probing` categories) trip a per-IP auto-ban in active mode, and nginx drops the classic root scanner paths (`/.env`, `/wp-login.php`, `/.git/config`, …) at the edge with `444`. This does not affect legitimate agent traffic to the gateway verbs.

## Internal Agent Mesh Exemption

Agents reach the orchestrator DIRECTLY on the docker bridge (no nginx hop), HMAC-authenticated — the guard's WAF/IP-ban/rate-limit is meant for the EXTERNAL attack surface arriving through nginx, not for that already-authenticated internal traffic. A `whitelist` of loopback (`127.0.0.1`/`::1`) plus docker's default bridge address-pool range (`172.16.0.0/12`) skips WAF/ban/rate-limit checks entirely for requests from those addresses — without it, an ordinary journal/note body tripping a WAF signature would IP-ban the whole agent container, wedging every subsequent verb call (`dm`, `i_am_idle`, ...) behind it.

This whitelist is deliberately narrow — NOT the full RFC1918 range. `10.0.0.0/8` and `192.168.0.0/16` are excluded on purpose: those also cover any real LAN client hitting nginx, not just the docker mesh, and with `trusted_proxy_depth=1` a genuine LAN browser's real IP survives the one XFF hop, so including them would let real external traffic skip the WAF right alongside agent traffic. A known ceiling remains: this can't distinguish a real docker-bridge peer from host-loopback/NAT'd traffic landing on the same address family, so a host-proxied chain (e.g. Tailscale Serve terminating on the host before nginx) can still resolve into this range and ride the exemption — see `ROBOCO_GUARD_TRUSTED_CHAIN_PEERS` above for the separate mechanism that scopes that specific shape.
