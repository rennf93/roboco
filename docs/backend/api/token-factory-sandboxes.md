# Token Factory Sandboxes - the run_sandbox_tests verb

Dev/QA-scoped gateway verb that executes a task's test suite inside a Nebius Token Factory Sandbox: an ephemeral microVM (Cloud Hypervisor) that boots in milliseconds, runs one shell expression, and is destroyed after the execution. This is the hackathon-track integration: the "run and test code" leg of the agent loop runs on Nebius infrastructure instead of the agent container's own shell, with the same envelope/evidence trail as every other verb.

## Surface

- MCP tool + route + verb: `run_sandbox_tests` (`POST /api/v1/do/run_sandbox_tests`, `roboco/mcp/do_server.py`, `roboco/api/routes/v1/do.py`, `roboco/services/gateway/content_actions.py`). Role-scoped to dev + QA (`_DEV_DO`/`_QA_DO` in `roboco/services/gateway/role_config.py`; no coordinator/board manifest carries it).
- Request body (`RunSandboxTestsRequest`): `command` (required shell command, 1-2000 chars), `image` (optional sandbox image ref; default `token_factory_sandboxes_image`), `timeout_seconds` (optional, 30-3600; default `token_factory_sandboxes_timeout_seconds`).
- REST client: `roboco/llm/providers/nebius_sandboxes.py` (never-raise, mirrors the `probe_*` shape discipline).

## Flow

1. Guards: flag off; no claimed project-bound task; no stored Nebius key; workspace/team/project unresolvable; `git archive` failure; archive over the upload ceiling.
2. The orchestrator runs `git archive --format=tar.gz HEAD` in the agent's workspace (worktree when one exists on disk, else the clone root) and uploads it to `POST {sandboxes}/v1/files` (octet-stream, returns a content-addressed `{uuid, sha256, size}`).
3. `POST {sandboxes}/v1/instances` spawns a disposable instance with the uploaded uuid in `files` and the composed shell expression (`mkdir -p /workspace && tar -xzf ... && cd /workspace && <command>`), `shell: true`, `timeout` from settings, `truncate_output_at` bounded.
4. The verb polls the `Location` operation URL until `SUCCESS/FAILED/CANCELLED`, cancelling best-effort on its own deadline, then flattens `metadata.result` (`state.exit_code`, `stdout`/`stderr` streams, `resources.cost`/`elapsed_time`).
5. The result rides in the envelope's `evidence.sandbox` (exit code, output tails, cost, duration, instance/operation uuids) and a journal entry records the run; the caller's task is heartbeated before and after the wait.

## Configuration (all default-off or inert)

| Setting | Env | Default |
| --- | --- | --- |
| `token_factory_sandboxes_enabled` | `ROBOCO_TOKEN_FACTORY_SANDBOXES_ENABLED` | `false` (also on the panel's Feature Flags card) |
| `token_factory_sandboxes_base_url` | `ROBOCO_TOKEN_FACTORY_SANDBOXES_BASE_URL` | `https://api.tokenfactory.nebius.com/sandboxes` |
| `token_factory_sandboxes_project_id` | `ROBOCO_TOKEN_FACTORY_SANDBOXES_PROJECT_ID` | empty - MUST be set, the API 400s without the `Project` header |
| `token_factory_sandboxes_image` | `ROBOCO_TOKEN_FACTORY_SANDBOXES_IMAGE` | `tag:python:3.12` |
| `token_factory_sandboxes_timeout_seconds` | `ROBOCO_TOKEN_FACTORY_SANDBOXES_TIMEOUT_SECONDS` | `900` |
| `token_factory_sandboxes_max_archive_bytes` | `ROBOCO_TOKEN_FACTORY_SANDBOXES_MAX_ARCHIVE_BYTES` | `67108864` (64 MiB) |

The Sandboxes API authenticates with the SAME stored Nebius API key as the inference provider (`Authorization: Bearer`), plus the optional `Project` header for scoped accounts.

## Safety posture

- Default-off feature flag; when off (or on any sandbox failure) QA keeps its container shell - the local path is unchanged, never bypassed-away.
- `command` is agent-authored free text: excluded from the signature WAF (`_WAF_FREETEXT_BODY_FIELDS`) to avoid false positives on test commands, while the route keeps mesh WAF scanning; the command only ever executes inside the ephemeral sandbox VM, not on the orchestrator host.
- The workspace archive contains committed HEAD only (tracked files, no `.env`, no uncommitted secrets), capped at the upload ceiling.
- The microVM is `disposable` (no image persisted) and destroyed after the run; a hung run is cancelled at the polling deadline.

## Live verification status (2026-09-14, hackathon key)

- Bearer auth with the stored Nebius key: VERIFIED (GET /v1/models 200; the same key authenticates the Sandboxes endpoints).
- `Project` header: REQUIRED (the API 400s "Missing Project header" without it). The console's project id (the `aiproject-...` id from Project settings) is the correct header value - identities resolve with it; the service-account id embedded in the key also passes identity but is NOT the project.
- Instance spawn with the trial key: still 403 "Insufficient permissions: spawn or spawn_disposable" WITH the correct project id - the key's service account lacks the Sandboxes spawn permission. Grant it console-side (Organization/Project settings -> Sandboxes, or an API key with sandbox scopes). Until then `run_sandbox_tests` degrades to a 403 error string and agents keep their local shells.
- The regional inference host (`api.tokenfactory.<region>.nebius.com`) does NOT route the Sandboxes service (nginx 404) - the sandboxes base stays the global `api.tokenfactory.nebius.com/sandboxes` default.
- Inference on the global host verified end-to-end separately (GLM-5.3-Flash chat completion, 200 + metered usage, 27 tokens).
- Operation polling/result shape (`metadata.result`) remains spec-derived - the flattener degrades to an error string if the live shape drifts.
