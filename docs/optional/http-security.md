# HTTP security hardening (fastapi-guard)

RoboCo can front its API with a [fastapi-guard](https://pypi.org/project/fastapi-guard/) security layer — IP and rate controls, a signature WAF, security headers, cloud-provider and honeypot checks, an emergency kill switch, and three RoboCo-specific content validators — for when you expose the panel/API beyond a trusted LAN.

The layer is **off by default**. When `ROBOCO_GUARD_ENABLED` is unset, `create_app` never mounts the middleware and the request path is byte-for-byte unchanged; the per-route guard decorators are harmless no-ops (`roboco/security.py`). A personal NAS deploy needs none of this — it's built for cloud/public hosting.

## What it does

Armed, the middleware sits outermost and applies, per route:

- **Rate limiting & size caps** — a baseline throttle plus tighter per-endpoint limits on sensitive routes (provider-key writes, CEO release ops, agent verbs).
- **A signature WAF** — SQLi/XSS/path-traversal/etc. detection on request bodies, headers, params, and path.
- **Security headers** — HSTS, CSP, `X-Frame-Options`, `nosniff`, referrer and permissions policies on every response.
- **Cloud-provider & honeypot checks** — block datacenter egress on the highest-value routes; form-trap honeypots on human-facing POSTs.
- **Three RoboCo custom validators** the stock WAF cannot cover — **prompt-injection**, **secret-exfil**, and **internal-SSRF** scanning on the prompt-facing and agent-content surfaces.
- **An emergency lockdown** (`ROBOCO_GUARD_EMERGENCY`) — a flip-on-without-redeploy switch that blocks every non-whitelisted IP during an active attack.

Behind nginx, guard trusts the single proxy hop for the real client IP, excludes WebSocket upgrades and health/docs paths, and keeps RoboCo's own CORS.

## Passive first, then enforce

Guard has two enforcement postures, and the intended rollout is **passive → active**:

| `ROBOCO_GUARD_PASSIVE_MODE` | Behaviour |
|---|---|
| `true` (calibrate) | **Log-only.** Detections are logged but **never block** — legit *and* malicious requests pass through. Safe to arm on live traffic to surface false positives first. |
| `false` (enforce) | Detections **block** the request. |

!!! warning "Calibrate before you enforce"
    RoboCo's request bodies *are* code, SQL, diffs, file paths, HTML, and URLs — task specs, agent notes and commits, RAG queries, git bodies, chat. A stock WAF reads that legitimate traffic as attacks. RoboCo ships a calibration (`excluded_detection_body_fields` in `build_security_config`, derived from the real request models) that excludes those free-text fields from WAF scanning so active mode does **not** false-positive — while the WAF stays active on every structured (id/enum/slug/branch) field and the prompt-injection / secret-exfil / SSRF validators keep firing regardless. Even so, arm **passive first** on your own deployment, watch the logs for any straggler false positive, then flip to active.

The NAS composes arm the guard in passive/log-only mode by default (`ROBOCO_GUARD_PASSIVE_MODE=true`, `ROBOCO_GUARD_FAIL_SECURE=false`) so a deploy calibrates against real traffic before you enforce.

## Scanner honeytrap & auto-ban

Automated scanners probe every host for well-known soft spots (`/.env`, `/wp-login.php`, `/phpmyadmin`, `/.git/config`, …). RoboCo turns those probes against the scanner, in two layers matched to where the traffic actually lands — behind nginx only `/api`, `/ws`, `/health`, and `/ready` reach the orchestrator, so guard can only see probes on those paths:

- **Guard adaptive ban (the `/api` surface).** The guard `threat_ban_config` carries `recon` / `sensitive_file` / `cms_probing` categories. A scanner probing those fingerprints on an `/api` path is detected on the URL-path scan, and repeated probes from one IP trip a per-IP auto-ban (redis-backed, 24h). This only bans in **active** mode (passive logs the recon hit) and requires redis (the 24h ban exceeds the in-memory cap).
- **nginx edge-drop (the classic root paths).** The classic scanner paths never reach the orchestrator, so nginx drops them at the edge with `444` (closes the connection, returns nothing) before they touch the panel. It's anchored to known scanner fingerprints — `/.well-known` and every real panel/API route are untouched — and is always on, independent of `ROBOCO_GUARD_ENABLED`.

## Fail-secure

`ROBOCO_GUARD_FAIL_SECURE` decides what happens if a security check itself errors: `true` (default) fails **closed** — block the request — which is the right default for public hosting. The personal NAS compose overrides it to `false` so a guard-internal bug can never 500 your own deploy.

## Enable it

=== "Environment"

    ```bash
    ROBOCO_GUARD_ENABLED=true
    ROBOCO_GUARD_PASSIVE_MODE=true      # calibrate first; flip to false to enforce
    ROBOCO_GUARD_FAIL_SECURE=true       # false on a personal/NAS deploy
    ROBOCO_ENVIRONMENT=production        # drives enforce_https (relaxed in development)
    # ROBOCO_GUARD_EMERGENCY=true                 # attack lockdown kill switch
    # ROBOCO_GUARD_EMERGENCY_WHITELIST=1.2.3.4    # extra allowed IPs during lockdown
    # ROBOCO_GUARD_TELEMETRY_ENABLED=true         # + guard_agent_api_key + guard_project_id
    ```

=== "Panel"

    **Settings → Feature Flags** exposes the master switch. The passive/fail-secure/environment knobs are environment settings, not flags — set them in env.

    !!! note "Takes effect on the next backend restart"
        The flag persists in the settings store and applies on the **next backend restart**.

See the [environment reference](../deploy/env-reference.md#http-security-fastapi-guard) for the full flag list.

## Telemetry (optional, off)

`ROBOCO_GUARD_TELEMETRY_ENABLED` reports security events/metrics to a guard-core platform via guard-agent. It is **off by default** and **no data leaves the box** while off; flip it on and set `ROBOCO_GUARD_AGENT_API_KEY` + `ROBOCO_GUARD_PROJECT_ID` to enable.

## Next

→ [Environment reference](../deploy/env-reference.md#http-security-fastapi-guard) for every flag · [Deployment](../deploy/deployment.md) for where to set them · back to [Optional subsystems](index.md).
