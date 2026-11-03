# Gateway verb latency: where the time goes

Findings for task 1524afe3. The observed numbers come from the intake: `i_am_done` p50 140430 ms and p95 230323 ms (16 samples), `i_will_work_on` p95 80765 ms (23), `open_pr` p50 128 ms and p95 79446 ms (9), `claim_review` p95 54389 ms (19). The breakdown below is derived from reading the verb code paths and the budgets they run under. Nothing here is a production trace: the per-phase `verb_phase` log events added by this task are what confirm or refute each claim, and the log queries to run are at the end. Each claim carries a confidence.

## How the phases are now measured

`roboco/utils/timing.py` provides `timed_phase(verb, phase, **fields)`, a sync context manager that logs one structlog `verb_phase` event with `duration_ms` and `outcome` (warning level at or above 5000 ms). It costs two `monotonic()` reads and one log line, so it stays on permanently. It is wired into:

| Verb | Phases emitted (`verb` / `phase`) |
|---|---|
| `i_am_done` | `tracing_gates`, `field_gates`, `branch_push`, `behind_base`, `quality_gate`, `toolchain_guard`, `conventions_gate`, `runner_chain`, `post_submit`; inside `branch_push`: `push_task_branch` / `codegen` and `push_task_branch` / `push` |
| `open_pr` | `task_lock_wait`, `briefing`, `runner_chain`; inside the runner: `side_effect` / `push_branch`, `side_effect` / `create_pr`, and `create_pr` / `ensure_base_on_remote`, `entire_link_before_pr`, `forge_post_pr`, `forge_ensure_labels`, `forge_add_labels` |
| `i_will_work_on` | `briefing`, `claim_plan_start` (the claim, plan, start chain plus branch and worktree provisioning) |
| `claim_review` | `briefing`, `evidence_build`; inside it `evidence_git`, `evidence_conventions` |
| every composed verb | `atomic` / `<action>` and `side_effect` / `<name>` from the verb runner dispatch |

## i_am_done: the pre-submit quality gate dominates

`i_am_done` runs a chain of guards in `_i_am_done_gate` (`roboco/services/gateway/choreographer/dev.py`) and only then the composed submit chain. Per phase:

- `tracing_gates`, `field_gates`, `toolchain_guard`, `runner_chain`, `post_submit`: a handful of DB reads and writes inside one request transaction. Expected tens to a few hundred milliseconds each. Confidence high that none of these is the 140 s.
- `branch_push`: `GitService.push_task_branch` runs `_run_codegen_and_commit` (a throwaway-container command only when the project sets `codegen_command`) and then a `git push` bounded by `git_network_timeout_seconds` (120 s default). Normally seconds; a stall here is bounded at 120 s.
- `behind_base`: one `git fetch` against the resolved parent. Seconds.
- `quality_gate`: `GitService.run_pre_submit_quality_gate` resolves the task worktree and calls `run_quality_commands`, which starts a throwaway container from the orchestrator image (`run_in_workspace_container`) and runs the project's fast gate. For this repo that is `make gate`, which depends on `sync` (a `uv sync`) and then runs `ruff format --check .`, `ruff check .`, `mypy roboco/ tests/` (about 1500 source files), `xenon` and `lint-imports`. Each submit pays container start, a venv sync, and a mypy run, and nothing in the container design carries a warm mypy cache or a synced venv from the previous run. The gate has a 600 s ceiling (`_GATE_TIMEOUT_SECONDS`) and the verb a 900 s ceiling (`flow_verb_slow_timeout_seconds`), which is consistent with a p50 of 140 s and a p95 of 230 s. This is the single largest phase. Confidence medium-high: it is the only phase with unbounded-by-design CPU work of that size, but no per-phase timing existed to prove it before this change.
- `conventions_gate` (only when `ROBOCO_CONVENTIONS_ENABLED`): a tree-sitter validator run over the changed files, bounded by `_CONVENTIONS_VALIDATOR_TIMEOUT_SECONDS`. A secondary cost.

The intake hypothesis (inline gate or self-verify run) is therefore right, with one refinement: the cost is the cold, from-scratch execution of `make gate` in a fresh container on every submit, not the existence of a gate.

### Recommended fix for i_am_done

1. First, make the gate cheap rather than asynchronous. Keep a per-worktree cache across gate runs: mount a persistent `.mypy_cache` and uv cache directory for the clone into the throwaway container, and skip the `sync` step when the lockfile hash is unchanged since the last gate run for that worktree. mypy is incremental; a warm cache typically turns a minutes-long full-tree check into seconds for a small diff. This changes no gate semantics, so `i_am_done` still cannot reach QA on a red gate.
2. Then, if the warm gate is still over the agent-side timeout budget, move it to a background job: `i_am_done` records a `gate_pending` marker and returns, the orchestrator runs the gate and calls back, a green callback performs the `verifying` to `awaiting_qa` transition, and a red one writes the failure to `remediate` for the developer. The invariant to keep: nothing reaches QA until the gate has passed. This is a larger change (new marker, callback path, tests for pass and fail through the callback) and should wait for the numbers from step 1.
3. The possibilities-matrix fast path (`ROBOCO_POSSIBILITIES_MATRIX_ENABLED`) already skips the local gate when the PR's CI is green. Arming it is the zero-code option for work that is already done; it does not help first submits whose CI has not run yet.

## open_pr: the median is cheap, the tail is a tree lock held across a CLI call

The median of 128 ms confirms the verb itself is DB reads plus one forge POST. The tail is a stall, not load, and the code points at one cause.

`GitService.create_pr` calls `link_before_pr` (`roboco/services/entire_trails/drain.py`) before the PR POST. When the Entire trails bridge is armed for the project, `link_before_pr` opens a session and calls `_lock_tree(session, root_id, wait=True)`, which is a blocking `pg_advisory_xact_lock` keyed on the tree root. The background drain, `_drain_tree`, takes the same per-tree advisory lock (with the non-blocking form) and then runs `_run_row`, which executes one Entire CLI call inside that transaction. A CLI call is bounded by `entire_trails_cli_timeout_seconds` (60 s default, plus a kill grace). If a drain row for the same tree is in flight when a developer calls `open_pr`, `link_before_pr` waits for the whole CLI call, up to roughly 60 to 70 s, and only then continues to its own CLI link call and the forge POST. That fits the observed p95 of about 79 s: lock wait of about 60 s plus the normal 128 ms to a few seconds of work plus the link call. Meanwhile `open_pr` holds its own per-task advisory lock (`acquire_task_lock`) and the request transaction for the whole time, so a second `open_pr` on the same task also queues behind it. Because the lock key is the tree root, any sibling or ancestor task's drain row blocks every task in the tree, which is why it looks random.

Confidence: medium-high on the mechanism, which is read straight from the code, and medium on it being the only cause. It only applies when `ROBOCO_ENTIRE_TRAILS_ENABLED` is on and the project is opted in; the `entire_link_before_pr` phase and `task_lock_wait` will show it directly. Secondary, unproven contributors: `GitHubProvider._send` opens a fresh `httpx.AsyncClient` per call with the 30 s `git_command_timeout_seconds`, and `_apply_pr_labels` makes one `ensure_label` call per label plus one `add_labels` call, all sequential, so a slow forge can add tens of seconds; and `push_branch` is bounded at 120 s.

### Shipped: fix 1 (bounded tree-lock wait)

Fix 1 below is implemented (task a48da4df, PR #1429). `_lock_tree` in `roboco/services/entire_trails/drain.py` takes a new optional `wait_seconds`. When a blocking wait is requested with it, the function sets a transaction-local Postgres `lock_timeout` (via `set_config(..., true)`) before `pg_advisory_xact_lock`, and resets it to `0` once the lock is held. `link_before_pr` passes `settings.entire_trails_link_lock_wait_seconds`.

- **Setting:** `ROBOCO_ENTIRE_TRAILS_LINK_LOCK_WAIT_SECONDS`, a float greater than 0, default `5.0`. It is the longest `open_pr` waits for the tree lock before its Entire link.
- **On timeout:** Postgres raises a lock-timeout error. `link_before_pr`'s existing failure path catches it, queues the link in the outbox, and the PR opens anyway. No Entire CLI call is made on the verb path in that case. The worst-case tree-lock stall on `open_pr` is therefore about 5 s instead of the CLI timeout of roughly 60 to 70 s.
- **Uncontended path:** unchanged. Callers that do not pass `wait_seconds` (including the drain's own non-blocking `wait=False` try-lock) behave as before. `approve_before_merge` still waits unbounded on purpose.
- **Accepted cost:** a PR that opens while a drain row holds its tree lock past the bound may get an Entire-owned trail of its own, because the link was queued rather than made before the PR. The queued link then fails visibly (dead row, CEO alert), as described in `.claude/rules/entire-trails.md`.
- **Test:** `test_a_contended_tree_lock_queues_the_link_instead_of_stalling` (`tests/integration/entire_trails/test_entire_trails.py`) holds the tree lock from a second session, sets the wait to 0.3 s, and asserts `link_before_pr` returns in under 5 s, makes no CLI call, and leaves one queued `link` row for the branch.
- **Not done:** fixes 2 and 3 below (drain releasing the lock across the CLI call; concurrent label calls) remain open.

### Recommended fix for open_pr

1. Never wait unbounded on the tree lock from the verb path. In `link_before_pr`, set a short `lock_timeout` (for example 5 to 10 s) or use `pg_try_advisory_xact_lock` and, on failure, fall straight into the existing `_queue_unless_pending` path (the function already documents that a failure is queued and the PR opens anyway). The accepted cost is that, rarely, Entire gives that PR a trail of its own; that is far cheaper than a 60 s stall holding a request transaction and a task lock.
2. Stop the drain holding the tree lock across the CLI call. Claim the outbox row (mark it in flight with a compare-and-set, as the notification re-escalation sweep does), release the lock, run the CLI call, then record the result in a short second transaction. This removes the contention at its source and is also what makes fix 1 safe to relax later.
3. Run `_ensure_label` / `add_labels` best-effort calls concurrently (or after the response is built) so label latency never sits on the verb's critical path.

## i_will_work_on and claim_review

- `i_will_work_on` (p95 about 81 s): two places. The `full=True` briefing (`_briefing_for`) assembles institutional memory, collision context and journal highlights, and the `claim_plan_start` chain runs the claim, plan and start composed actions plus the fresh-claim branch and worktree provisioning (a clone reset and `uv sync --extra dev` on a fresh claim; the flow timeout comment in `foundation/policy/flow_timeouts.py` names the workspace clone at up to 300 s as the reason this verb has the slow budget). Confidence medium: the tail is almost certainly cold workspace provisioning, which the `claim_plan_start` and `briefing` phases will separate. Fix direction: pre-provision the worktree and venv when the dispatcher assigns the task, so the claim only attaches it.
- `claim_review` (p95 about 54 s): `_build_qa_claim_evidence` assembles the PR diff, the changed file list and the conventions findings under one shared `LegBudget(evidence_assembly_timeout_seconds)`, which defaults to 45 s. A p95 of 54 s is that 45 s budget plus the `full=True` briefing and the DB legs, so the p95 is the budget being exhausted on a cold or contended workspace, with the evidence marked degraded through `evidence_gaps`. Confidence medium-high. The `evidence_git` and `evidence_conventions` phases show which leg burns it. Fix direction: warm the reviewer's clone when the task enters `awaiting_qa`, or lower the budget so a cold clone degrades quickly instead of at 45 s.

## Confirming the findings from logs

Every phase logs `verb_phase` with `verb`, `phase`, `duration_ms`, `outcome`. To rank where a verb's time goes, group the events by `verb` and `phase` and look at p50 and p95 of `duration_ms`. The checks that settle the open questions:

- `i_am_done`: `quality_gate` should account for most of the verb's duration; if `branch_push` or `conventions_gate` is large instead, the gate fix above is the wrong lever.
- `open_pr`: a tail where `entire_link_before_pr` (and `task_lock_wait` for a duplicate call) is 55 s or more while `forge_post_pr` stays small confirms the tree-lock cause; a tail in `forge_post_pr`, `forge_ensure_labels` or `side_effect` / `push_branch` points at the forge or network instead.
- Any phase at 5000 ms or more is logged at warning level, so `level=warning verb_phase` is the quick stall finder.
