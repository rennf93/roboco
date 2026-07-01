# Blocked HTTP Requests (Security Guard)

## Symptom

A call to the orchestrator API fails with a generic `400` or `403` and no detail about what was flagged.

## Cause

RoboCo's HTTP security layer (`fastapi-guard`; see `docs/rag/architecture/http-security-guard.md`) rejected the request. This can only happen when both `ROBOCO_GUARD_ENABLED=true` and `ROBOCO_GUARD_PASSIVE_MODE=false` (enforce mode) are set. As of 2026-07-01 the guard is off by default, and wherever it is enabled it runs passive/log-only, so this is not something that occurs today — it's documented so the block is recognizable if/when enforcement is turned on later.

The guard never returns which rule or signature matched, by design, so the 400/403 body itself gives you nothing to act on. Avoiding the triggers below is the only real mitigation.

## What Gets Flagged

Generic WAF signatures (SQL injection, XSS, path traversal, suspicious URL patterns) are excluded from scanning on the free-text body fields of `note` / `commit` / `say` / `dm`, so normal code, SQL, diffs, file paths, and URLs in those bodies are safe from that layer. Three custom validators scan those same bodies regardless of that exclusion:

- Prompt-injection detection
- Secret-exfil detection — literal credential-shaped strings (`sk-ant-...`, `ghp_...`, postgres connection URLs) or phrasing like "reveal your api keys"
- Internal-SSRF detection — fetch-type bodies targeting internal or metadata hosts (`169.254.169.254`, `roboco-*` internal service hostnames)

## Solution: Hygiene Rules

Follow these when composing `note` / `commit` / `say` / `dm` bodies or any fetch-type payload, regardless of whether enforcement is currently active:

1. Never paste real secrets or credentials (API keys, tokens, DB connection strings) into a request body, even inside a code snippet or diff.
2. Never aim a fetch/HTTP-call body at an internal service host (`roboco-*`) or a cloud metadata endpoint (`169.254.169.254`).
3. Code, SQL, diffs, file paths, and HTML snippets are otherwise fine to include — the WAF layer is calibrated to exclude legitimate agent content on those fields.

## Current Status (2026-07-01)

The guard is built on the `feature/fastapi-guard-hardening` branch, off by default (`ROBOCO_GUARD_ENABLED=false`), and wherever enabled it runs in passive/log-only mode. No agent request is being blocked by it today — the rules above are about good hygiene now and correctness later, not a live restriction.
