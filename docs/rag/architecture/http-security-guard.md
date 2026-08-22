# HTTP Security Guard

## What It Is

RoboCo's HTTP request layer is protected by `fastapi-guard` (7.6.0, on `guard-core` 3.12.0), implemented in `roboco/security.py` and wired into the app in `roboco/api/app.py`'s `create_app`.

## Enable/Disable

| Variable | Default | Effect |
|----------|---------|--------|
| `ROBOCO_GUARD_ENABLED` | `false` | Master switch. Off = completely inert — no middleware is mounted, the request path is entirely unchanged, and nothing is logged or blocked. |
| `ROBOCO_GUARD_PASSIVE_MODE` | see below | When the guard is enabled, controls whether it blocks matching requests or only logs them. |
| `ROBOCO_GUARD_EMERGENCY_WHITELIST` | `` (empty) | Comma-separated IPs/CIDRs always allowed through in an active `ROBOCO_GUARD_EMERGENCY` lockdown, in addition to loopback. Empty = loopback only. |
| `ROBOCO_GUARD_TRUSTED_CHAIN_PEERS` | `` (empty) | Comma-separated exact IP address(es) — never a CIDR range — trusted to appear as a recorded proxy hop inside `X-Forwarded-For` beyond loopback, e.g. the docker bridge gateway a host-proxied Tailscale Serve chain terminates behind, so the resolved client is the real tailnet/LAN peer instead of that hop's own address. Empty = only a loopback rightmost hop ever peels. |
| `ROBOCO_GUARD_SCAN_RESPONSE_BODY` | `false` | Lets `return_pattern` behavior rules read the response body, not just the status code. Roboco's own status:404/status:401 rules never need it (see below); off by default is byte-for-byte unchanged behavior. |

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

## `trusted_proxies` Must Track the Whitelist

`build_security_config` passes guard-core a second, easily-confused list alongside the whitelist: `trusted_proxies` — the addresses guard treats as *proxy hops* when it walks `X-Forwarded-For` to depth `trusted_proxy_depth`. It is NOT the whitelist (which decides who skips WAF/ban/rate-limit), but the two MUST stay in lockstep, and on this deploy they're identical: `127.0.0.1`, `::1`, `172.16.0.0/12` — loopback plus docker's default bridge pool, the same set as `_INTERNAL_NETWORKS`.

The invariant: **whatever ranges the whitelist excludes, `trusted_proxies` must exclude too.** If `trusted_proxies` ever widens to cover a range the whitelist does not (the bug fixed in #811), a forwarded IP from that range is treated as a proxy *hop* rather than the real client — guard peels it, falls back to the connecting peer, and if that peer is itself whitelisted (a docker-bridge nginx in `172.16.0.0/12`), the request rides the exemption. Concretely: with `10.0.0.0/8` and `192.168.0.0/16` erroneously in `trusted_proxies`, a docker-bridge nginx forwarding `X-Forwarded-For: 192.168.1.50` (a real LAN client) made guard peel `192.168.1.50` as a "trusted hop," resolve the client to the whitelisted `172.18.x` peer, and return `200 OK` — the narrowed whitelist was never consulted for the LAN IP at all. The fix removed both LAN ranges from `trusted_proxies`; now guard resolves `192.168.1.50` as the real client, finds it outside `_INTERNAL_NETWORKS`, and blocks it. The companion case still holds: a docker-bridge peer with no XFF resolves to itself (in `172.16.0.0/12`) and stays exempt.

If you ever narrow or widen the whitelist, apply the same edit to `trusted_proxies` in the same commit — the two lists are one policy, split across two guard-core knobs.

The invariant is anchored by two layers of tests in `tests/unit/test_security_middleware.py`. The end-to-end test `test_nginx_forwarded_lan_client_is_not_whitelisted` drives the full `SecurityMiddleware` stack (including the `@deco.custom_validation` route on `/task`) with a docker-bridge peer forwarding `X-Forwarded-For: 192.168.1.50` and asserts the request is blocked (status != 200); it passes on guard-core 3.7.0+ (including the current 3.12.0 pin), where `IpSecurityCheck.check` (guard-core `core/checks/implementations/ip_security.py:197-208`) falls through to `_check_global_ip_restrictions` even when a `route_config` exists. Its companion `test_docker_bridge_peer_without_xff_is_whitelisted` asserts the same peer without XFF stays exempt. Two isolated unit tests — `test_extract_client_ip_forwarded_lan_not_peeled` and `test_is_ip_allowed_rejects_lan_ranges` — call `guard_core.utils` functions directly (no running server) as defense-in-depth, verifying that `extract_client_ip` resolves the LAN IP correctly and `is_ip_allowed` rejects it against the narrowed whitelist. An `INVARIANT` comment at the `trusted_proxies` definition in `build_security_config` (`roboco/security.py`) restates the must-mirror-`_INTERNAL_NETWORKS` rule in-line.

Note the guard-core version sensitivity: on 3.4.0, `IpSecurityCheck` had an early-return that skipped `_check_global_ip_restrictions` for routes with a `route_config` (any `@deco.custom_validation` route), so the e2e test failed because the resolved LAN IP was never consulted against the whitelist. guard-core 3.7.0 fixed this (the current 3.12.0 pin retains the fix) — the check falls through to the global IP restrictions regardless of `route_config` (the final `return await self._check_global_ip_restrictions(...)` at `ip_security.py:208` runs unconditionally after the route-level check returns `None`). The PR #817 in-path review failure was exactly this drift: the reviewer's local venv had downgraded to guard-core 3.4.0 while `uv.lock` pinned 3.7.0, so the e2e test failed in that environment and passes on the pinned version. Always run tests against the `uv.lock`-pinned version (via `make` targets, never bare `uv run`); a drifted local venv can silently downgrade guard-core and reintroduce the bypass.

## Excluded Paths: What Still Runs

`exclude_paths` (`/ws`, `/health`, `/healthz`, `/docs`, `/redoc`, `/openapi.json`, `/favicon.ico`, `/.well-known`, `/static`) no longer means "skip every check." As of guard-core 3.12.0, an excluded path still runs `route_config`, `ip_security`, and `rate_limit` (each check declares `enforced_on_excluded_paths=True`); only the signature WAF and behavioral tracking are skipped there.

In practice: a whitelisted client (the internal agent mesh, or an allowlisted tailnet peer) is unaffected on an excluded path, since `is_whitelisted` short-circuits both `rate_limit` and the global IP check before either does any work. A NON-whitelisted client hitting an excluded path past the global rate limit still gets `429`'d, where the old (pre-3.12.0) semantics let it through unmetered. A banned or blacklisted IP is still blocked (`403`) on an excluded path, where the old semantics fully bypassed it. Roboco relies on none of the old fully-bypassed behavior, so this is a tightening, not a regression, but it is worth knowing before adding a new path to `_EXCLUDE_PATHS`: it stops the WAF and behavioral tracking, not IP/rate enforcement.

`tests/unit/test_security_middleware.py`'s `TestExcludePathSemantics` pins all three cases against the real `SecurityMiddleware` harness.

## Global 404/401 Sweep Detection

Two log-only `global_behavior_rules` watch every route for calibration signal, never bans: a `return_pattern` rule on `status:404` (threshold 30 in 300s) flags a scanner-style sweep hitting nonexistent endpoints, and a companion rule on `status:401` (threshold 20 in 300s) flags credential-probing. Both are `action="log"` deliberately, since an internal agent with a stale HMAC token must never earn a ban, and bans override the whitelist.

The pattern is `status:404` / `status:401`, not a bare `404` / `401` substring. guard-core 3.12.0 validates `return_pattern` rules at `SecurityConfig` construction (and again on any later reassignment): a bare-substring or `json:`/`regex:` pattern requires reading the response body and is rejected unless `behavior_scan_response_body=True` (`ROBOCO_GUARD_SCAN_RESPONSE_BODY`, off by default, see the table above). `status:` patterns match the response status code directly and are exempt from that requirement. Before fastapi-guard 7.6.0 the body-based form was silently dead code (the adapter's `.body` raised, guard-core swallowed it), so switching to `status:` is a strict fix, not a behavior change: the 404 rule always meant to count 404 responses, but the bare form never fired at all.

## Behavioral Rule Redis Fail-Open Patch

`route_config.behavior_rules` (per-route `usage_monitor()`/`behavior_analysis()`) and `global_behavior_rules` (the 404/401 rules above) are invoked directly from `SecurityMiddleware.dispatch`/`_process_response` in the `guard` package, OUTSIDE `SecurityCheckPipeline`, the only place `redis_fail_open` is honored. `BehaviorTracker.track_endpoint_usage`/`track_return_pattern` call redis unguarded and raise `GuardRedisError` on a blip, which would otherwise fall through to roboco's generic exception handler as a `500` instead of failing open like every pipeline check does.

`roboco/security.py` patches all three call sites at import time: `BehavioralProcessor.process_usage_rules` (route-level usage/frequency rules), `process_return_rules` (route-level return_pattern rules, roboco has none of these today), and `process_global_return_rules` (the seam the status:404/401 rules actually run through). Each wraps the original method, catches `GuardRedisError`, and either re-raises (when `redis_fail_open=False`) or logs a warning and swallows it (when `redis_fail_open=True`, roboco's default). Verified still required at guard-core 3.12.0; the durable fix belongs upstream.

## Internal Readiness Probe

`apply_guard` calls `guard.status.add_status_route(app)` after mounting the middleware, exposing `GET /_guard/status` (`include_in_schema=False`). nginx only routes `/api` and `/ws` to the orchestrator, so this is reachable on the docker mesh / `localhost:8000` only, which is the intent: an internal readiness check, not a public endpoint.

## Immutable Config Collections

guard-core 3.12.0 freezes ten `SecurityConfig` collection fields after construction: `whitelist`/`blacklist`/`trusted_proxies` coerce to `tuple`, `enabled_detection_categories`/`muted_*`/`block_cloud_providers` coerce to `frozenset`, `threat_ban_config` coerces to `MappingProxyType`, and `global_behavior_rules` coerces to `tuple`. Construction from a plain list/set/dict still works; only in-place mutation after construction raises. `roboco/security.py`'s own module-level constants (`_THREAT_BAN_CONFIG`, `_BEHAVIOR_RULES`, `_guard_whitelist()`, the inline `trusted_proxies` literal) are declared as the frozen shape directly so static typing matches the runtime contract. `tests/unit/test_security.py::test_whitelist_is_immutable_after_construction` pins the new contract.

## Telemetry: Sensitive Headers

When `ROBOCO_GUARD_TELEMETRY_ENABLED` is armed, `_agent_kwargs()` sets `agent_sensitive_headers` to `x-agent-token`, `authorization`, `cookie`, and `x-api-key`, so HMAC/session material never leaves the box in a telemetry payload. Inert while telemetry is off (the whole `_agent_kwargs()` dict is empty).
