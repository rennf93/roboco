export const meta = {
  name: 'roboco-logic-audit-rerun',
  description: 'Targeted re-run: gateway-choreographer split into 4 scoped finders + 9 cross-cutting blind-spot finders, each adversarially verified',
  phases: [
    { title: 'GatewayFind', detail: '4 scoped finders over the gateway/choreographer core' },
    { title: 'BlindFind', detail: '9 cross-cutting blind-spot finders lost to the prior 429' },
    { title: 'Verify', detail: 'adversarial verify of each finding against the real code' },
  ],
}

const REPO = '/Users/renzof/Documents/GitHub/ZZZ/roboco-master/roboco'

const FINDINGS_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    subsystem: { type: 'string' },
    findings: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        properties: {
          title: { type: 'string' },
          severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
          category: { type: 'string', enum: ['race-condition', 'state-machine', 'missing-guard', 'concurrency', 'resource-leak', 'error-handling', 'ordering', 'assumption', 'edge-case', 'deadlock', 'inconsistency', 'auth-security', 'data-loss', 'performance', 'other'] },
          file: { type: 'string' },
          lines: { type: 'string' },
          description: { type: 'string' },
          impact: { type: 'string' },
          evidence: { type: 'string' },
        },
        required: ['title', 'severity', 'category', 'file', 'description', 'impact'],
      },
    },
  },
  required: ['subsystem', 'findings'],
}

const VERDICT_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    isReal: { type: 'boolean' },
    confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
    correctedSeverity: { type: 'string', enum: ['critical', 'high', 'medium', 'low', 'not-a-bug'] },
    reasoning: { type: 'string' },
    notes: { type: 'string' },
  },
  required: ['isReal', 'confidence', 'reasoning'],
}

const FINDER_INSTRUCTIONS = `You are a senior staff engineer doing a ruthless LOGICAL-GAP audit (NOT style/naming/coverage). A logical gap = where code CAN reach an invalid/unintended state or silently do the wrong thing: races/TOCTOU, state-machine holes (a transition that skips a required side-effect like audit emit / notification / index update, or re-enters a terminal state), missing guards (unhandled None/empty/wrong-type), swallowed errors that continue into an inconsistent state, fire-and-forget that drops a load-bearing failure, ordering/sequencing (work before its dependency, check after the mutation it guards), resource leaks (git clones / docker containers / DB conns / file handles / subprocesses), cross-site inconsistency (two sites that should agree but can diverge), data-loss (a path that drops commits / overwrites work / merges the wrong PR / loses a task).

METHOD — be rigorous AND bounded (do NOT read whole huge files end-to-end, you will blow your context):
1. Use Grep to locate the relevant functions/symbols by name, then Read ONLY the specific function bodies and their immediate call sites (use Read with offset/limit, or smart_unfold). Read full small files; for _impl.py (6300+ lines) NEVER read it whole — always targeted slices.
2. For each candidate gap, confirm by reading the actual code path end-to-end. Note exact file:line. Quote the offending lines in evidence.
3. Prefer fewer HIGH-CONFIDENCE findings over many speculative ones, but cover your scope exhaustively. Aim for 4-10 solid findings.
4. Be concrete: file:line, trigger condition, resulting bad state, blast radius. If you can't find concrete evidence, don't report it.
5. Cite real paths relative to repo root (e.g. roboco/services/gateway/choreographer/_impl.py:123).

Return ONLY confirmed evidence-backed findings via the structured schema.`

const GATEWAY_FINDERS = [
  { key: 'gw-verb-runner', prompt: `Gateway verb runner & INVALID_STATE re-check. In ${REPO}/roboco/services/gateway/choreographer/_impl.py, Grep for the verb dispatch / run path and the re-check-after-each-atomic-action logic (search: "INVALID_STATE", "re-fetch", "re-issue", "_run_composed", "can_invoke_intent", "atomic"). Read those function slices (targeted, not the whole file). Question: after each composed atomic action the runner re-checks the task and on a concurrent mid-verb state change fails fast with INVALID_STATE — but are there atomic sequences where a side-effect (commit, PR open, notification, audit) already executed BEFORE the re-check catches the state change, leaving an external effect with a rejected envelope? Does the remediate hint always match a recoverable path? Can a verb return ok with next=... while the task is actually in a different state than the envelope claims?` },
  { key: 'gw-claim-locking', prompt: `Claim locking & claim_guards. Read ${REPO}/roboco/services/gateway/claim_guards.py fully, and in choreographer/_impl.py Grep for the claim path and the _COORDINATOR_ROLES skip (search: "_COORDINATOR_ROLES", "already_active", "paused", "unmet_dependency", "_run_claim_guards", "claim"). Read those slices. Question: the coordinator PM roles skip the one-task-per-developer guards (already_active/paused) and only the unmet_dependency guard holds — can a PM claim a second root while the first is genuinely active in a way that corrupts shared state (e.g. same clone, same integration branch)? Does the paused guard's exclusion of the target task itself allow a PM to un-pause-and-reclaim in a window that double-spawns? Is there a TOCTOU between the guard check and the claim DB write? Is claim locking actually serialized by a DB row lock or just a status check?` },
  { key: 'gw-tracing-content', prompt: `Tracing gate & content_actions. Read ${REPO}/roboco/services/gateway/content_actions.py (the _caller_role derivation, commit/note/say/dm/evidence handlers, the note fire-and-forget indexing), and in choreographer/_impl.py Grep for the tracing gate that can block delegate (search: "tracing", "circuit_open", "delegate", "note", "section"). Read those slices. Question: the 2026-06-27 meltdown was minimax PMs emitting section={} for note(scope=handoff) -> "done — Field required" -> note circuit_open -> tracing gate blocked delegate. Is the resumption done/next now reliably top-level typed params across do_server + NoteRequest + route + content_actions (check each site agrees — cross-site inconsistency)? Can a note persist succeed while RAG indexing fails and the failure path leaves tracing in circuit_open with no auto-recovery? Can content_actions run a commit/say/dm as a derived role with no token check (cross-ref the do route auth)?` },
  { key: 'gw-role-config-servers', prompt: `role_config + flow/do server request handlers + remediation. Read ${REPO}/roboco/services/gateway/role_config.py fully, and the flow + do MCP server request handlers (Grep under roboco/services/gateway/ for the server dispatch: "roboco-flow", "roboco-do", "call_tool", "handle", "Envelope", "remediate", "missing"). Read those handler slices. Question: does every verb's manifest entry match lifecycle.intents_for_role (cross-site inconsistency between role_config and lifecycle)? Can a verb listed in a role's manifest but NOT in lifecycle.intents_for_role be invoked (or vice versa)? Are remediation hints ever wrong/stale (pointing at a verb/state that no longer exists)? Does the do server enforce the per-role content-tool removal (Auditor no say/dm, pr_reviewer no agent comms, prompter/secretary note+evidence only) at the handler, or only at manifest build time?` },
]

const BLIND_SPOTS = [
  { key: 'db-transaction-boundaries', prompt: `DB transaction boundaries across verb composition. In ${REPO}, audit whether composed verb sequences (choreographer _impl.py — Grep "compose", "atomic", sequence of task_svc calls) run inside a single DB transaction or as separate commits. Question: can a multi-step verb (e.g. claim -> set status -> create work session -> emit audit) partially commit — step 2 succeeds, step 3 raises, leaving a claimed task with no work session, or a status set with no audit row? Is each atomic action its own transaction with no rollback of prior actions on a later step failure? Trace the DB session/transaction handling in the service layer (roboco/services/task.py, work_session.py) and the gateway. Does a failure mid-composition leave the task in a state inconsistent with the returned envelope?` },
  { key: 'make-quality-gate-correctness', prompt: `The make-quality gate's own load-bearing correctness. In ${REPO}, audit the gate that agents run (make quality / ruff / mypy / xenon / pytest) and how the orchestrator/services parse its result. Grep for "make quality", "gate", "ruff", "mypy", "xenon", "quality" under roboco/services/. Question: does the gate parse exit codes correctly, or can a partial pass (ruff clean but mypy crashed) report green? Can a missing tool (ruff: command not found) be misread as pass instead of fail? Is the toolchain-match check (ROBOCO_TOOLCHAIN_MATCH_ENABLED) actually load-bearing or bypassable? Can an agent's i_am_done accept a gate result that was never actually run (cached/stale)?` },
  { key: 'subprocess-tempfile-lockfile-leaks', prompt: `Subprocess / tempfile / lockfile leaks. In ${REPO}, Grep for "subprocess", "Popen", "asyncio.create_subprocess", "tempfile", "NamedTemporaryFile", "mkdtemp", "lock", "flock", ".lock" under roboco/services/ and roboco/llm/providers/. Question: are spawned subprocesses (git, gh, grok, make, docker exec) always awaited and reaped (no zombies, no orphaned processes on cancel/timeout)? Are temp dirs/files cleaned up on exception paths (try/finally)? Are lockfiles removed if the holder crashes? Can a cancelled subprocess leak a container or hold a file lock that blocks the next agent?` },
  { key: 'ceo-physical-merge-reconcile', prompt: `CEO-physical-merge reconcile gap. Context: the CEO deploys the LOCAL tree and physically merges PRs; origin/master is NOT the source of truth. In ${REPO}, audit every site that assumes origin/master == deployed state — Grep "origin/master", "default_branch", "latest_ci_conclusion", "get_latest_ci_conclusion", "read_clone", "HEAD" under roboco/services/. Question: do CI-watch / release-readiness / self-heal read CI from origin/master and act on a green that the local tree doesn't have (or vice versa)? Does release_executor cut a tag against a clone of origin that the CEO's local tree has already diverged from? Can a release be cut on commits the CEO never merged? Does the reaper/orchestrator reconcile running containers against origin or local?` },
  { key: 'graceful-shutdown-lifespan', prompt: `Graceful shutdown, bg tasks, FastAPI lifespan ordering. In ${REPO}, Grep for "lifespan", "shutdown", "cancel", "_bg_tasks", "BackgroundTasks", "asyncio.gather", "atexit", "signal" under roboco/services/orchestrator.py and roboco/api/. Question: on orchestrator shutdown, are in-flight background tasks (respawn persist, waiting records, token sweep, engine loops) cancelled and drained, or abandoned mid-write (the respawn_tracker upsert race was exactly fire-and-forget)? Does lifespan index docs before the DB/ollama are ready (the known startup-order issue)? Can a SIGTERM during a verb leave a task claimed with no agent? Are the engine loops (_ci_watch_loop, _dep_update_loop, _release_manager_loop) stopped cleanly or do they raise on shutdown?` },
  { key: 'verb-idempotency-retry', prompt: `Verb idempotency under agent retry. In ${REPO}, audit whether gateway verbs are safe to call twice when an agent retries after a transient error. Grep in choreographer/_impl.py and content_actions.py for "open_pr", "commit", "complete", "submit_root", "submit_up", "delegate". Question: if an agent calls open_pr twice (first succeeded but agent didn't see the response), does it create a second PR or return the existing one? Is commit idempotent (no double commit on retry)? Is complete/submit_root idempotent (no double-merge, no double PR)? Can an agent retry-into a second delegate creating duplicate subtasks? Trace the idempotency keys / "already exists" guards on each mutating verb.` },
  { key: 'cross-project-id-collisions', prompt: `Cross-project identifier collisions beyond pr_number. In ${REPO}, the PR-merge cross-repo collision fix added project_id scoping for pr_number. Audit OTHER identifiers that might collide across projects. Grep "branch_name", "work_session", "slug", "container_name", "agent_slug", "workspace" under roboco/services/. Question: are branch names namespaced per project (two projects can both have feature/backend/ABC12345)? Are workspaces/containers per-project (container name collision across projects)? Are agent slugs global — can two projects' agents share a slug and a workspace path? Does any DB query filter by project_id where it must (the pr_number fix showed at least one site forgot)? Look for queries keyed only on a non-unique identifier without project_id.` },
  { key: 'time-timezone-reaper', prompt: `Time / timezone consistency in reaper & scheduling. In ${REPO}, Grep for "datetime", "utcnow", "now(", "timezone", "timestamp", "heartbeat", "stale", "expires", "ttl", "Interval", "seconds" under roboco/services/orchestrator.py, redis rate-limit, and the engines. Question: does the reaper compare heartbeat ages in a consistent timezone (UTC) or mix UTC DB columns with local now()? Can a TZ mismatch make the reaper over-reap (heartbeat appears stale when it isn't) or under-reap? Are engine intervals / token-expiry TTLs computed against the same clock? Can a restart-during-DST-transition mis-schedule a loop?` },
  { key: 'reconnect-replay-agent-ws', prompt: `Reconnect / replay after restart for agent WebSocket streams. In ${REPO}, read roboco/api/websocket.py and roboco/api/websocket_bridge.py. Question: when an agent container reconnects its WS (or the orchestrator restarts), does it get a replay of missed events or only live-forward from reconnect? Can a missed event (notification, status broadcast) be permanently lost on a brief disconnect? Does ConnectionManager clean up dead connections (no leak of sockets for dead agents)? Can a broadcast to a closed socket raise and kill the bridge forwarder (the per-event _handle_* path)? Is there backpressure if an agent's WS is slow (unbounded queue)?` },
]

function finderPrompt(p) { return `${p}\n\n${FINDER_INSTRUCTIONS}` }

function verifyPrompt(f) {
  return `You are an adversarial verifier for a claimed logical gap in RoboCo at ${REPO}. Default: SKEPTICISM. A finding is REAL only if you confirm it by reading the actual code. If you can't confirm, or the code handles it, isReal=false.

Claimed finding:
- title: ${f.title}
- severity (claimed): ${f.severity}
- category: ${f.category}
- file: ${f.file}
- lines: ${f.lines || '(not specified)'}
- description: ${f.description}
- impact: ${f.impact}
- evidence: ${f.evidence || '(none quoted)'}

JOB:
1. Read ${f.file} at/around the cited lines (targeted Read with offset/limit; for _impl.py NEVER read whole — read ~120 lines around the cited line). Read enough surrounding context (call sites, the function body, the guard claimed missing) to judge.
2. Decide: REAL logical gap, or already handled / not reachable / misread / style nit? Trace the actual trigger.
3. isReal=true + correctedSeverity (downgrade or upgrade) if real; isReal=false + correctedSeverity='not-a-bug' if not.
4. Reasoning MUST quote the lines you actually read. Don't rubber-stamp; don't reject reflexively.

Return the verdict via the structured schema.`
}

const allFinders = [
  ...GATEWAY_FINDERS.map((f) => ({ ...f, phase: 'GatewayFind' })),
  ...BLIND_SPOTS.map((f) => ({ ...f, phase: 'BlindFind' })),
]

phase('GatewayFind')
const results = await pipeline(
  allFinders,
  (f) => agent(finderPrompt(f.prompt), { label: `find:${f.key}`, phase: f.phase, schema: FINDINGS_SCHEMA, effort: 'high' }),
  (review, f) => parallel(((review && review.findings) || []).map((fi) => () =>
    agent(verifyPrompt(fi), { label: `verify:${String(fi.title).slice(0, 36)}`, phase: 'Verify', schema: VERDICT_SCHEMA, effort: 'high' })
      .then((v) => ({ ...fi, verdict: v, subsystem: (review && review.subsystem) || ('rerun:' + f.key) }))
      .catch(() => null)
  ))
)

const confirmed = results.flat().filter(Boolean).filter((f) => f.verdict && f.verdict.isReal)
const bySev = {}
for (const f of confirmed) { const s = (f.verdict && f.verdict.correctedSeverity) || f.severity; bySev[s] = (bySev[s] || 0) + 1 }
log(`Re-run: ${results.flat().filter(Boolean).length} raw, ${confirmed.length} confirmed`)

return {
  rerunConfirmed: confirmed.length,
  bySeverity: bySev,
  findings: confirmed.map((f) => ({
    subsystem: f.subsystem,
    title: f.title,
    severity: (f.verdict && f.verdict.correctedSeverity) || f.severity,
    category: f.category,
    file: f.file,
    lines: f.lines,
    description: f.description,
    impact: f.impact,
    evidence: f.evidence,
    confidence: f.verdict && f.verdict.confidence,
    verifierReasoning: f.verdict && f.verdict.reasoning,
  })).sort((a, b) => {
    const o = { critical: 0, high: 1, medium: 2, low: 3, 'not-a-bug': 4 }
    return (o[a.severity] ?? 5) - (o[b.severity] ?? 5)
  }),
}