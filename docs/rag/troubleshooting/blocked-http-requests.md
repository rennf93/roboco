# Blocked HTTP Requests (Security Guard)

## Symptom

A call to the orchestrator API fails with a generic `400` or `403` and no detail about what was flagged.

## Cause

RoboCo's HTTP security layer (`fastapi-guard`; see `docs/rag/architecture/http-security-guard.md`) rejected the request. This can only happen when both `ROBOCO_GUARD_ENABLED=true` and `ROBOCO_GUARD_PASSIVE_MODE=false` (enforce mode) are set. As of 2026-07-19 that is the default on the NAS build compose — a matching request is actually blocked there, not just logged.

The guard never returns which rule or signature matched, by design, so the 400/403 body itself gives you nothing to act on. Avoiding the triggers below is the only real mitigation.

## What Gets Flagged

Generic WAF signatures (SQL injection, XSS, path traversal, suspicious URL patterns) are excluded from scanning on the free-text body fields of `note` / `commit` / `dm`, so normal code, SQL, diffs, file paths, and URLs in those bodies are safe from that layer. Three custom validators scan those same bodies regardless of that exclusion:

- Prompt-injection detection — phrasing like "ignore previous instructions", or "bypass/disable/override the guard/filter/restriction" (a real risk in this repo's own security-work commit messages and notes: `roboco/security.py`'s own doctring vocabulary uses "guard", "bypass", "disable" constantly)
- Secret-exfil detection — literal credential-shaped strings (`sk-ant-...`, `ghp_...`, postgres connection URLs) **or the literal doc pattern `ROBOCO_ENCRYPTION_KEY=<...>` / `ROBOCO_AGENT_AUTH_SECRET=<...>` / `FERNET_KEY=<...>`** (matches `CLAUDE.md` and `.env.example`'s own env-var documentation verbatim — quoting or editing those lines in a `note`/`commit` body trips this) or phrasing like "reveal your api keys"
- Internal-SSRF detection — fetch-type bodies targeting internal or metadata hosts (`169.254.169.254`, `roboco-*` internal service hostnames)

The three custom validators scan the raw request body regardless of which top-level field the text sits in — unlike the WAF's field exclusion, there is no safe field for these three.

## Solution: Hygiene Rules

Follow these when composing `note` / `commit` / `dm` bodies or any fetch-type payload:

1. Never paste real secrets or credentials (API keys, tokens, DB connection strings) into a request body, even inside a code snippet or diff.
2. When discussing an env var like `ROBOCO_ENCRYPTION_KEY` in a note/commit body, don't write it as `NAME=value` (even a placeholder value) — write `ROBOCO_ENCRYPTION_KEY` and describe the value separately, e.g. "set to a generated Fernet key", to avoid the `NAME=<10+ chars>` credential-shape match.
3. Avoid "bypass/override/disable the guard/filter/restriction/safety" phrasing in commit messages or notes about this security layer itself — describe the change without that verb+noun adjacency (e.g. "excludes X from the WAF scan" instead of "bypasses the guard's WAF scan").
4. Never aim a fetch/HTTP-call body at an internal service host (`roboco-*`) or a cloud metadata endpoint (`169.254.169.254`).
5. Code, SQL, diffs, file paths, and HTML snippets are otherwise fine to include — the WAF layer is calibrated to exclude legitimate agent content on those fields (this exclusion does not cover the three custom validators above).

## Current Status (2026-07-19)

`ROBOCO_GUARD_ENABLED` is off by default in config; the NAS build compose arms it ON with `ROBOCO_GUARD_PASSIVE_MODE=false` (enforce). A matching request on that deploy is genuinely blocked, not just logged — the rules above are a live restriction there, not just future-proofing. The registry compose and a bare config default both stay off.
