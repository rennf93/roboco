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
| `token_factory_sandboxes_image` | `ROBOCO_TOKEN_FACTORY_SANDBOXES_IMAGE` | `tag:python:3.13` (this repo requires Python >=3.13) |
| `token_factory_sandboxes_timeout_seconds` | `ROBOCO_TOKEN_FACTORY_SANDBOXES_TIMEOUT_SECONDS` | `900` |
| `token_factory_sandboxes_max_archive_bytes` | `ROBOCO_TOKEN_FACTORY_SANDBOXES_MAX_ARCHIVE_BYTES` | `67108864` (64 MiB) |

The Sandboxes API authenticates with the SAME stored Nebius API key as the inference provider (`Authorization: Bearer`), plus the optional `Project` header for scoped accounts.

## Safety posture

- Default-off feature flag; when off (or on any sandbox failure) QA keeps its container shell - the local path is unchanged, never bypassed-away.
- `command` is agent-authored free text: excluded from the signature WAF (`_WAF_FREETEXT_BODY_FIELDS`) to avoid false positives on test commands, while the route keeps mesh WAF scanning; the command only ever executes inside the ephemeral sandbox VM, not on the orchestrator host.
- The workspace archive contains committed HEAD only (tracked files, no `.env`, no uncommitted secrets), capped at the upload ceiling.
- The microVM is `disposable` (no image persisted) and destroyed after the run; a hung run is cancelled at the polling deadline.

## Live verification status (2026-09-15, beta access granted - FULLY VERIFIED)

- Bearer auth with the stored Nebius key + the `Project` header (the console's `aiproject-...` project id): VERIFIED.
- Image catalog: `GET /v1/images` lists 100+ tagged images; `tag:python:3.12` exists (runs Python 3.12.13). The default moved to `tag:python:3.13` on 2026-10-15 (listed in the console's Sandboxes > Images alongside `python:3.14` and `astral/uv:python3.13-trixie`; not yet run end to end).
- Instance spawn: VERIFIED (201 + host-rooted `Location: /sandboxes/v1/operations/<uuid>` header).
- Operation polling and the result contract: VERIFIED (`metadata.result.state.exit_code`, `stdout.value`, `resources.cost`/`elapsed_time`).
- The full client flow (upload -> spawn -> extraction -> command run -> poll -> flatten) verified end to end with this exact client: exit 0, stdout intact, metered cost about 0.0003 per echo-scale run.
- One integration bug the live run caught and fixed: the poll URL must be normalized from the host-rooted Location (see wait_sandbox_operation); before the fix every poll double-pathed into empty 404s.
- Beta limits: up to 50 concurrent operations.
