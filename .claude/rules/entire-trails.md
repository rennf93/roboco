---
paths:
  - "roboco/services/entire_trails/**"
  - "roboco/services/entire_credentials.py"
  - "roboco/api/routes/entire.py"
  - "roboco/runtime/engines/entire_trails.py"
  - "roboco/utils/entire_markers.py"
  - "panel/src/components/settings/entire-trails-form.tsx"
  - "tests/integration/entire_trails/**"
  - "tests/fixtures/entire_cli/**"
---

# Entire trails bridge

**What it is.** Behind `ROBOCO_ENTIRE_TRAILS_ENABLED` (default off) and a per-project opt-in (`projects.entire_trails_enabled`, default off, GitHub projects only), the dispatcher mirrors each task tree onto one Entire trail (design: trail #24 on entire.com, CEO decisions 2026-10-29). The tree is a coordination root, or a MegaTask umbrella spanning several repositories. The root's intent, plan and task table are the trail's text, every branch is a change, review findings are trail findings, and the CEO's approval is a trail approval. The mirror is one way: RoboCo's database stays canonical and RoboCo's merge path is the one that merges. Agents never see Entire or its token; the token lives encrypted in `entire_credentials` and reaches only the CLI subprocess's environment.

**Constraints Entire 0.12.2 imposes (spiked on rennf93/roboco, recorded in `tests/fixtures/entire_cli/0.12.2/`):**
- A change can never move to another trail: `unlink` and a relink both return 409. A branch must be linked to the right trail the first time, before its first PR opens. Otherwise Entire gives the PR a trail of its own, and the later link fails visibly (dead row, CEO alert).
- Linking a branch with no commits ahead of its base creates the change with no PR. Once commits reach origin, Entire's bot opens a PR 2.5–4 minutes later. Linking a branch that already has commits opens a draft PR at once. A PR opened by RoboCo on a linked branch is adopted into its change, and about four minutes later Entire prepends its link block to the body.
- That is why dev branches link at claim (zero commits), and PM-claimed integration branches link right before their own PR (`link_before_pr` in `GitService.create_pr`). Linked at claim, an integration branch would get a bot PR minutes after its first child merge. When Entire did open the PR first (an integration branch, or a dev branch force-pushed by `sync_branch` before `open_pr`), the 422 "already exists" path adopts it: RoboCo's title and body, Entire's markers kept, taken out of draft through GraphQL (GitHub's REST API cannot, and GitHub never merges a draft).
- Retries must be byte-identical. `trail create` under the same idempotency key with any other field gives a 409, which is why `entire_trails.create_fields` is frozen when the tree is bridged and the current text follows as an update. `finding add` under the same client id with other fields gives `client_id_conflict`, which is why a finding's text is rendered once into its outbox row. The client-id window is 24 h, so a retried add first looks the finding up by client id (the ledger id).
- Resolving, dismissing, approving and closing are all safe to repeat. A closed trail refuses new changes with a 409, so a trail closes last; the hooks stop queueing for a tree once its close is queued.
- An approval pins the head sha it approves; a later push leaves it behind. The CEO's approval is therefore mirrored inline, right before RoboCo merges the root PR, never queued ahead of time.

**Invariants.**
- Outbox rows are written in the lifecycle's own transaction (each hook in a savepoint that logs and never raises), so an operation reaches Entire if and only if the RoboCo change it mirrors commits.
- The drain runs one tree's rows in id order under a per-tree advisory lock, one transaction per row. A dead row holds its tree until the CEO retries it (Settings → Feature Flags → Entire trails).
- `entire_trail_changes.task_id` is deliberately not a foreign key: the inline link must never take a share lock on a task row its waiting request may hold `FOR UPDATE`.
- A tree is bridged only if its first branch was cut on an opted-in project. A tree already running when its project opts in keeps Entire's own per-PR trails, since its PRs' changes cannot be moved.
- The trail text is not hooked per event. The drain's `reconcile_trails` re-syncs an open trail whose tasks' `updated_at` passed its `synced_at`, and closes one whose top task ended; that also catches the status writes that bypass `_validate_and_set_status`.

**Verification.** Unit tests replay the recorded CLI output; `test_entire_trails_contract.py` fails when `ENTIRE_VERSION` moves without re-recording `tests/fixtures/entire_cli/<version>/` (help text and `--json` output of every command the bridge runs). The integration suite runs the real hooks and drain against Postgres with that replay, checking every flag against the recorded help. `tests/integration/entire_trails/test_entire_trails_live.py` (opt-in `ROBOCO_ENTIRE_TRAILS_LIVE=1`) drives a whole tree through the real service and diffs `trail show` / `finding list` / `trail approvals` against the database.

**Open.** S2: the dispatcher's own long-lived Entire token, which the CEO creates (the CLI honours `ENTIRE_TOKEN` verbatim; a login JWT from `entire auth token` expires). Turning off Entire's auto-trails and shadow PRs per repository is not in the CLI (Entire docs or support). Brain in briefings is phase 2.
