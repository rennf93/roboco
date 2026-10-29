## Purpose

The Decisions service: typed "System One" decisions at points that would otherwise be a full LLM call or a hardcoded heuristic. A call site sends a structured state and a batch of typed questions (noul yes/no, choice, score) and gets back answers with calibrated confidence; the calling code accepts, rejects, or falls back on a per-pilot confidence floor. Each integration point is a named **pilot** (`PILOT_SLUGS`) with its own mode (off, shadow, on).

Three tiers serve one wire format, resolved per call by `resolver.py`: Clef (the default, a self-hosted 27B llama.cpp model in the `roboco-decisions` sidecar, no key and no spend), Laya (the same sidecar's 421M model, used when Clef is disabled or unhealthy), and OpenRouter (opt-in, metered, only when the CEO opts in and the AI Provider screen holds a key). The floor below all tiers is "no verdict": master flag off, a pilot off, or no healthy tier means every call site does exactly what it did before the service existed. Decisions only add behaviour; no verdict may satisfy, skip or auto-pass a lifecycle gate, and nothing materializes on a verdict alone.

Every verdict is buffered into `decision_log` (migrations 105 to 107). The trajectory labeler (`roboco/runtime/engines/decisions_labeler.py`, mapped in `docs/map/orchestrator.md`) later grades those rows against what the tasks table says happened, `export.py` turns labelled rows into a fine-tune corpus, and the Decisions Audit Board Program gives the Auditor a daily per-pilot review and prunes the log past its retention window.

## Files

| Path | Role | LOC |
|---|---|---|
| `roboco/services/decisions/__init__.py` | Package exports and the service doctrine: three tiers behind one wire format, fail-open, one attempt, every verdict logged, shadow before on, no verdict passes a lifecycle gate | 85 |
| `roboco/services/decisions/resolver.py` | Per-call tier resolution: master flag off is the floor; else Clef if enabled and healthy, else Laya, else OpenRouter if opted in with a key; 30-second cached sidecar health, 60-second cached OpenRouter key, one CEO notification per boot when the opted-in key is missing | 296 |
| `roboco/services/decisions/client.py` | `DecisionsClient`: the one HTTP client for every tier; client-side state caps and the stage-2 total-budget cap for self-hosted tiers, a per-process circuit breaker, `decide` returns None for no verdict | 461 |
| `roboco/services/decisions/schemas.py` | Pydantic models of the Decisions wire format: `NoulQuestion`, `ChoiceQuestion`, `ScoreQuestion`, `DecisionAnswer`, `DecisionResult`, and the lenient `parse_decisions_payload` | 174 |
| `roboco/services/decisions/pilots.py` | `PilotMode`, `PILOT_SLUGS`, `pilot_mode` (the single mode chokepoint), `decide_for_pilot`, `log_action`, and the core pilots: self-heal transient gate, parking lane, complexity score, preflight diff, test triage, transcript note, A2A steer gate, tool spotlight | 871 |
| `roboco/services/decisions/pilots_content.py` | Content-lane pilots: intake pre-routing, X mention triage, segment classification, vault prefilter, secretary directive kind and assignee, Telegram free-text gate, memory distill gate, changelog highlights, proactive standards domain, decision-note sufficiency | 979 |
| `roboco/services/decisions/pilots_dispatch.py` | Dispatch-lane pilots: external PR triage, idle-session reaping, context pruning, budget wrap-up, delta brief, review queue priority and depth, Board evidence skip, respawn verdict, submit-now confidence, park cause, PM closure safety, silent-exit pickup | 1267 |
| `roboco/services/decisions/pilots_gateway.py` | Gateway-lane pilots: PM plan quality, commit-intent hint, findings-to-diff mapping, idle legitimacy, branch staleness, lesson pruning, assembled-branch coherence | 749 |
| `roboco/services/decisions/pilots_infra.py` | Infrastructure-lane pilots: injection screen, collision edges, CI-watch route, heal severity, release-worthy urgency, second-review high stakes, notification semantic dedup, Board due-early and rotation target, stranded-batch lane, Coroner warranted, dep-update risk, release risk advisory | 1220 |
| `roboco/services/decisions/context.py` | Recipient work context composed for the A2A steer-gate verdict | 203 |
| `roboco/services/decisions/persist.py` | Fire-and-forget `decision_log` persistence: buffered `record_decision` with a background flusher, and outcome stamping (`record_outcome`, `record_question_outcomes`) on a subject's unlabeled rows | 310 |
| `roboco/services/decisions/outcomes.py` | Outcome label vocabulary shared by outcome producers and the corpus exporter, with the slug-to-gold-probability mapping and gold-shape validation | 327 |
| `roboco/services/decisions/trajectory.py` | Task-trajectory outcome labeler: grades unlabeled rows of trajectory-class pilots against what the tasks table recorded afterwards, plus stamp helpers for parking, secretary confirmation and release decisions, and decision-to-learning provenance links | 1421 |
| `roboco/services/decisions/export.py` | Exports labelled `decision_log` rows as the JSONL fine-tune corpus (`python -m roboco.services.decisions.export`) | 286 |
| `roboco/services/decisions/spend_guard.py` | Per-tier spend accounting from `usage.cost`; alerts the CEO on two HTTP 402s in a rolling hour or when the OpenRouter total crosses `decisions_cost_alert_usd` | 194 |
| `roboco/services/decisions_audit_engine.py` | Decisions Audit Board Program (Auditor): daily cycle prunes `decision_log` past retention, originates one held review task, and injects the previous day's per-pilot aggregates into the Auditor's prompt | 275 |
| `docker/decisions.Dockerfile` | CPU-only `roboco-decisions` sidecar image: two llama.cpp decision servers (Clef and Laya weights) behind the FastAPI proxy | 159 |
| `docker/decisions/entrypoint.sh` | Sidecar supervisor: starts the Clef (127.0.0.1:8110) and Laya (127.0.0.1:8111) llama-servers, waits for each `/health`, then runs the proxy in the foreground | 178 |
| `docker/decisions/server.py` | Sidecar FastAPI proxy serving the OpenRouter Decisions wire shape at `POST /api/alpha/decisions`, forwarding to the routed llama.cpp backend and normalizing answers; `GET /health` returns 503 while Clef is unreachable so traffic moves to a fallback tier | 512 |

## Key Symbols

| Name | Kind | File:Line | Responsibility |
|---|---|---|---|
| `resolve_endpoint` | function | `roboco/services/decisions/resolver.py:68` | The tier for this call, or None for the floor |
| `check_openrouter_fallback_at_startup` | function | `roboco/services/decisions/resolver.py:98` | Startup check for an opted-in OpenRouter fallback without a key |
| `DecisionsClient.decide` | method | `roboco/services/decisions/client.py:275` | Ask one batched question set; None means no verdict |
| `get_decisions_client` | function | `roboco/services/decisions/client.py:459` | Process-wide client (per-process circuit breaker) |
| `parse_decisions_payload` | function | `roboco/services/decisions/schemas.py:143` | Leniently parse a Decisions response body |
| `PilotMode` | StrEnum | `roboco/services/decisions/pilots.py:59` | `off`, `shadow`, `on` |
| `pilot_mode` | function | `roboco/services/decisions/pilots.py:129` | Mode for one pilot: master flag, then settings row, then env lists, else off |
| `decide_for_pilot` | function | `roboco/services/decisions/pilots.py:153` | Resolve mode and tier, ask one batched question set, log the verdict |
| `steer_gate` | function | `roboco/services/decisions/pilots.py:702` | How a peer DM should reach its recipient |
| `external_pr_triage` | function | `roboco/services/decisions/pilots_dispatch.py:145` | Score an inbound PR's review priority and screen its text |
| `respawn_verdict` | function | `roboco/services/decisions/pilots_dispatch.py:837` | Classify a respawn candidate the strike counter is gating |
| `plan_quality` | function | `roboco/services/decisions/pilots_gateway.py:121` | Score a structurally valid PM decomposition plan |
| `injection_screen` | function | `roboco/services/decisions/pilots_infra.py:200` | Noul screen for injection in external text |
| `record_decision` | function | `roboco/services/decisions/persist.py:89` | Buffer one decision row and schedule a flusher |
| `record_outcome` | function | `roboco/services/decisions/persist.py:141` | Label every unlabeled row of one subject |
| `run_trajectory_pass` | function | `roboco/services/decisions/trajectory.py:359` | One grading pass over every trajectory-class pilot |
| `export_corpus` | function | `roboco/services/decisions/export.py:243` | Stream labelled rows to JSONL; returns the row count |
| `record_402` | function | `roboco/services/decisions/spend_guard.py:55` | Count one HTTP 402 and alert on the second within an hour |
| `DecisionsAuditEngine.run_cycle` | method | `roboco/services/decisions_audit_engine.py:68` | Daily pass: prune, originate the held review, assemble the brief |
| `decisions` | function | `docker/decisions/server.py:430` | `POST /api/alpha/decisions` proxy handler |

## Data Flow

1. A call site (a dispatch engine, the Choreographer, an autonomy engine, A2A, the secretary or intake path) calls its pilot helper in `pilots*.py`.
2. The helper reads the pilot's mode through `pilot_mode` (master flag off forces off; then the `decisions.pilot.<slug>` settings row; then the `decisions_pilots_on` / `decisions_pilots_shadow` env lists; else off). Off returns the pre-Decisions behaviour immediately.
3. `decide_for_pilot` resolves a tier (`resolve_endpoint`, with a 30-second cached sidecar health probe), caps the state to the tier's budget, and makes one call through `DecisionsClient.decide` (one attempt, no retries, a per-process circuit breaker). Any failure returns no verdict.
4. The verdict and its evidence are buffered by `record_decision` and flushed to `decision_log` in the background; `spend_guard.record_spend` counts cost per tier, and the CEO is alerted on two HTTP 402s in a rolling hour or when the OpenRouter total crosses `decisions_cost_alert_usd`.
5. In shadow mode the verdict is only logged. In on mode the helper acts on it only above its confidence floor.
6. Later, outcome producers (`record_outcome`, `record_question_outcomes`) and the trajectory labeler (`run_trajectory_pass`) attach ground-truth labels; `export.py` streams labelled rows to JSONL; `DecisionsAuditEngine` summarizes the previous day for the Auditor and prunes old rows.

## Logical Tree

```
roboco/services/decisions/
├── __init__.py — package exports and doctrine
├── resolver.py — per-call tier choice (Clef, Laya, OpenRouter, floor)
├── client.py — DecisionsClient: caps, circuit breaker, one HTTP call
├── schemas.py — wire models: NoulQuestion, ChoiceQuestion, ScoreQuestion, DecisionResult
├── pilots.py — pilot_mode, decide_for_pilot, core pilots (self-heal, parking, complexity, preflight, triage, transcript, steer, tool spotlight)
├── pilots_content.py — content-lane pilots (intake, X mentions, segments, vault, secretary, Telegram, memory distill, changelog, domain, decision notes)
├── pilots_dispatch.py — dispatch-lane pilots (external PRs, idle reaping, context pruning, budget wrap-up, delta brief, review queue, respawn, park cause, closure, silent exit)
├── pilots_gateway.py — gateway-lane pilots (plan quality, commit intent, findings map, idle legitimacy, branch staleness, lesson prune, assembled coherence)
├── pilots_infra.py — infrastructure-lane pilots (injection, collision edges, CI route, heal severity, release, second review, notification dedup, Board cadence, stranded batches, Coroner, dep-update and release risk)
├── context.py — recipient work context for the A2A steer gate
├── persist.py — buffered decision_log writes and outcome stamping
├── outcomes.py — outcome label vocabulary and gold mapping
├── trajectory.py — task-trajectory outcome labeler
├── export.py — labelled rows to the fine-tune corpus
└── spend_guard.py — per-tier spend accounting and 402 alerts
roboco/services/decisions_audit_engine.py — Board Program #15 (Auditor)
docker/decisions.Dockerfile, docker/decisions/{entrypoint.sh,server.py} — the roboco-decisions sidecar
```

## Dependencies

- Reads `roboco.config.settings` (`decisions_*`), the settings store (`decisions.pilot.<slug>` rows), and the OpenRouter key from the AI Provider configuration (Fernet-decrypted, cached 60 seconds).
- Writes `decision_log` (`DecisionLogTable` in `roboco/db/tables.py`).
- Called from about 36 modules outside the package: the dispatch engines under `roboco/runtime/engines/`, the Choreographer and PR gate, `commit_validator.py`, `a2a.py`, `secretary.py`, `prompter.py`, `telegram_inbound.py`, `sequencing.py`, `second_review.py`, `notification_dedup.py`, the self-heal, CI-watch, dep-update, release-manager, strategy, Coroner, vault-intake, video and X engines, `memory_distiller.py`, `proactive.py`, `extraction.py`, `task.py`, `board_programs.py`, `foundation/policy/injection_guard.py`, and `api/app.py` (startup key check).

## Entry Points

- `decide_for_pilot` (`pilots.py`), reached through each pilot helper.
- `check_openrouter_fallback_at_startup` (`resolver.py`), called at app startup.
- `run_trajectory_pass` (`trajectory.py`), driven by the decisions labeler loop.
- `python -m roboco.services.decisions.export` (`export.py:main`), the corpus export.
- `DecisionsAuditEngine.run_cycle`, originated by `BoardProgramEngine` under the `decisions_audit` key.
- The sidecar: `GET /health` and `POST /api/alpha/decisions` in `docker/decisions/server.py`.

## Config Flags

| Setting | Default | Effect |
|---|---|---|
| `decisions_enabled` | False | Master flag; off forces every pilot off |
| `decisions_base_url` | `http://roboco-decisions:8100` | Sidecar address for the Clef and Laya tiers |
| `decisions_tier_clef_enabled` / `decisions_tier_laya_enabled` | True / True | Self-hosted tiers |
| `decisions_tier_openrouter_enabled` | False | Opt-in metered fallback tier |
| `decisions_timeout_s` / `decisions_clef_timeout_s` / `decisions_openrouter_timeout_s` | 5 / 60 / 2 seconds | Per-tier call budgets |
| `decisions_cost_alert_usd` | 1.0 | Spend alert threshold |
| `decisions_pilots_on` / `decisions_pilots_shadow` | empty | Env arming lists; a settings-store row wins |
| `decisions_log_retention_days` | 90 | `decision_log` pruning by the audit cycle |
| `decisions_labeler_interval_seconds` / `decisions_trajectory_grade_after_hours` / `decisions_trajectory_rot_hours` | 900 / 48 / 168 | Labeler cadence and grading windows |
| `board_program.decisions_audit.enabled` (settings row) | off | Arms the Decisions Audit Board Program |

## Gotchas

- Fail-open everywhere: a timeout, an unhealthy tier, a parse failure or an open circuit returns no verdict, and the call site keeps its pre-Decisions behaviour.
- The sidecar's answer normalization (boolean noul coercion, confidence recovered from the reported answer's probability) is load-bearing: the orchestrator's parser rejects booleans on purpose, and an entropy-style confidence would fail every 0.6 to 0.8 gating floor.
- A missing OpenRouter key with the fallback opted in sends one ack-required CEO notification per boot, not one per call.
- The build compose arms the master flag for the NAS deploy; the registry compose ships it off.
