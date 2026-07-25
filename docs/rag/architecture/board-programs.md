# Board Program Registry

## What It Is

Fourteen periodic/event-driven origination cycles — the Board actually doing strategic work instead of only reviewing it — ride one generic registry + engine instead of bespoke per-engine loops. `BoardProgram` (`roboco/foundation/policy/board_programs.py`) is a frozen registry entry; `PROGRAMS` holds all fourteen. `BoardProgramEngine` (`roboco/services/board_programs.py`) is the shared trigger/dedup/originate/LEARN machinery every entry rides. Every artifact any program produces is HELD — the CEO is the only path to materialization. Nothing auto-starts, auto-posts, or auto-merges.

The lifecycle is uniform across every program:

```
TRIGGER → EXPLORE → PROPOSE → DECIDE → MATERIALIZE → LEARN
 (loop)   (solo      (one verb  (CEO     (per-program   (outcome fed to
           spawn,     call,      queue,   materializer)   next cycle's
           read-only  held)      per-item)                exploration prompt)
```

## The fourteen programs

| Key | Role | Trigger | Scope | Source marker | Proposal verb | Materializer |
|-----|------|---------|-------|----------------|----------------|---------------|
| `roadmap` (Printer) | product_owner | cron, weekly | org | `board_roadmap` | `propose_roadmap` | backlog tasks, per-item CEO decision |
| `pest_control` (Pest Control) | product_owner | cron, weekly + rework-spike metric | project | `board_pest_control` | `propose_bug_hunt` | backlog tasks, per-item CEO decision |
| `spackle` (Spackle) | product_owner | cron, biweekly | project | `board_spackle` | `propose_gap_fill` | backlog tasks, per-item CEO decision |
| `scales` (Scales) | product_owner | cron, monthly | org | `board_scales` | `propose_rebalance` | mutates a LIVE task in place (reprioritize/cancel) on approval — never creates a task |
| `dogfood` (Dogfood) | product_owner | event (release-publish hook or CEO run-now) | project | `board_dogfood` | `propose_friction_fixes` | backlog tasks, per-item CEO decision |
| `periscope` (Periscope) | head_marketing | cron, weekly | org | `board_periscope` | `propose_market_brief` | held report only, no task, no per-item decision |
| `megaphone` (Megaphone) | head_marketing | cron, every 3 days | org | `board_megaphone` | `propose_editorial_post` | held X draft (existing X queue) |
| `mirror` (Mirror) | head_marketing | cron, quarterly | project | `board_mirror` | `propose_messaging_fixes` | backlog docs tasks, per-item CEO decision |
| `barfly` (Barfly) | head_marketing | cron, every 2 days | org | `board_barfly` | `propose_conversation_replies` | held X draft per reply (existing X queue) |
| `war_room` (War Room) | head_marketing | event (release-publish hook or CEO run-now) | org | `board_war_room` | `propose_campaign` | N held X drafts as one batch (existing X queue) |
| `x_feature` (feature spotlight) | head_marketing | cron, default daily | org | `x_feature_exploration` | `propose_feature_spotlight` | held X draft (existing X queue) |
| `coroner` (Coroner) | auditor | event only — no cron | org | `board_coroner` | `propose_postmortem` | a held process-change item, or a playbook drafted directly when `process_change.kind='playbook'` |
| `librarian` (Librarian) | auditor | cron, biweekly | org | `board_librarian` | `propose_playbook_drafts` | 1-3 DRAFT playbooks via `PlaybookService` directly, into the same curation queue |
| `sentinel` (Sentinel) | auditor | cron, weekly | org | `board_sentinel` | `propose_quality_report` | held report only, no task, no per-item decision |

`roadmap` and `x_feature` predate the registry; migrating them onto it was deliberately behavior-identical (Phase 1). The other twelve are new (Phase 2/3).

## Enable/Disable — no master flag

Unlike most feature-flagged subsystems, there is **no `ROBOCO_BOARD_PROGRAMS_ENABLED`**. Every program is armed independently through `program_armed(session, key)` — THE single chokepoint every origination path routes through (the cron loop, the metric-predicate check, the CEO's "run now", the strategy-engine idle trigger). It reads a settings-store row `board_program.{key}.enabled`; only `roadmap` and `x_feature` fall back to a legacy env flag (`ROBOCO_ROADMAP_ENGINE_ENABLED`; `ROBOCO_X_ENGINE_ENABLED` AND `ROBOCO_X_FEATURE_SPOTLIGHT_ENABLED`) when no settings-store row exists yet. Every other program is settings-store-only and defaults **off** — a fresh deployment originates nothing until the CEO flips a toggle on the Board Programs panel page.

The one env knob among the twelve new programs: `ROBOCO_PEST_REWORK_THRESHOLD` (default `0.3`) — the 7-day rework rate above which Pest Control's metric predicate opens a cycle off-schedule, on top of its own weekly cron. No other new program has a compose-level setting; per-program cadence overrides (when set) also live in the settings store, not env.

## Scope and dual-polarity project participation

`projects.board_programs` (migration `088`, nullable jsonb list of strings) governs which projects a program runs against or outputs into:

- **`scope="project"`** programs (they read one repo: Pest Control, Spackle, Dogfood, Mirror — see the table above) need an **affirmative opt-in**: the plain key (`"pest_control"`) must be present in the list, or the program has no opted-in project and a cycle is never even opened (`_scope_gate`). Null/absent = out.
- **`scope="org"`** programs (they read the org's own process or the external market: roadmap, Scales, Periscope, Megaphone, Barfly, War Room, Coroner, Librarian, Sentinel, x_feature) run **org-wide by default** and are excluded per-project only by the opposite-polarity entry: `"!roadmap"` opts a project OUT of that program's output. Null/absent = in.

One pure helper, `project_participates(program, board_programs_field)`, implements both polarities; `validate_board_programs_field` rejects an unknown key, a meaningless `"!"` on a project-scoped key, or a meaningless plain key on an org-scoped key. Panel: the project settings page's budget/ops card renders project-scoped programs as "participates in" checkboxes and org-scoped programs as "excluded from" checkboxes.

## The engine mechanics

`BoardProgramEngine.run_due_programs` (called by the orchestrator's `_board_program_loop` on a floor interval — the shortest registered cadence, clamped 300s-3600s) walks every CRON program: enabled → scope-gated → dedup-checked against the `board_program_cycles` ledger (migration `087`; one row per cycle, `closed_at IS NULL` = open, auto-closed the moment its exploration task goes terminal) → cron-due (`program_due`) → originate via the program's `_ORIGINATORS` callable (each program's own engine's `run_cycle`, e.g. `PestControlEngine.run_cycle`) → record the cycle row. It then separately runs every registered metric predicate (`_METRIC_PREDICATES`, today only Pest Control's rework-spike check) — cheap gates (scope, dedup) run BEFORE the predicate itself, so a multi-query metric check never runs on a tick that was always going to be rejected.

`open_program_cycle(key)` is the same enabled+scope+dedup path minus the cron-due check — the seam the CEO's panel "run now" button and the Strategy Engine's `idle` trigger both use (`docs/rag/architecture/company-layer.md`). **Only Printer is wired to the strategy-engine idle trigger** — the design intent to also trigger Coroner off `stranded_blocked` was not built; Coroner is event-only, triggered exclusively by its own three hooks (see below).

Every exploration is a **solo one-shot spawn** — the board dispatcher's `_dispatch_board_program_exploration` (a dict-dispatch table keyed by `task['source']`) routes it to a dedicated one-shot dispatcher (e.g. `_dispatch_pest_control_exploration`) that bypasses the two-reviewer board-review gate (`_handle_board_assigned_task`) entirely — a program cycle has exactly one author, never a PO+HoM pair. Every dispatcher reuses the `_board_dispatched` one-shot tracker + respawn breaker. Every program source is in the dispatchers' skip bucket (`_is_non_dev_dispatch_source`) — a program exploration task is never mistaken for delivery work.

### Coroner's event hooks

Coroner is the one program with `trigger=event` AND a real trigger wired outside the loop (unlike `war_room`, whose event trigger IS also reachable through `open_program_cycle` for a CEO on-demand run — Coroner has no on-demand path, only incidents). Three chokepoints call `CoronerEngine.open_for_incident(task_id, kind=...)` directly:

- `TaskService`'s bounce transition — a task crossing into `needs_revision` for the 3rd+ time (`revision_count >= 3`), `kind="bounced"`.
- `TaskService`'s cancel path — a task cancelled after real work had started, `kind="cancelled"`.
- The orchestrator's budget-block path — a task blocked on a budget breach, `kind="budget"`.

Only one autopsy is open at a time (the same `board_program_cycles` dedup every cron program uses); a second incident firing while one is open waits.

### War Room and Dogfood's release hooks

`WarRoomEngine.open_for_release(...)` and the release-publish path both bypass `_ORIGINATORS` entirely and open a cycle directly, mirroring Coroner's `open_for_incident` shape — War Room's release brief carries the version + curated highlights so posts never invent a feature; a CEO on-demand run (blank brief) instead rides the ordinary `open_program_cycle("war_room")` path. Dogfood similarly has a real `_ORIGINATORS["dogfood"]` binding (unlike Coroner's always-`None` stub) — a walk needs no external incident id, just the next opted-in project in rotation, so both the release-publish hook and a CEO "run now" open a cycle through the ordinary path.

### Rotation for project-scoped programs

Pest Control, Spackle, Mirror, and Dogfood share one round-robin picker, `pick_rotation_target`: among a program's opted-in projects, never-explored beats explored, else the oldest `last_opened_at` wins (read from the programs' own exploration tasks, not the LEARN ledger — a project-scoped program can run its engine's `run_cycle` directly, outside `BoardProgramEngine`, so the ledger alone would be blind to some cycles).

## LEARN

`BoardProgramEngine.record_decision(program_key, item_ref, verdict, reason, exploration_task_id=...)` accrues one CEO approve/reject onto the cycle row's `decisions` jsonb column, incrementing `items_proposed`/`items_approved`/`items_rejected`. `prior_cycle_context(program_key, limit=2)` renders the last two CLOSED cycles ("proposed N, approved N; rejected: item — reason") for injection into the NEXT cycle's exploration prompt — every producer of a per-item decision (`RoadmapService`, `PestControlService`-equivalent per-program services) calls this, so a program stops re-proposing something the CEO already rejected without explanation. This is the one genuinely new pipeline stage the registry introduced; the pre-registry roadmap/spotlight engines had no memory of prior outcomes at all.

## Panel and API

`GET /api/board-programs` and `POST /api/board-programs/{key}/run-now` (`roboco/api/routes/board_programs.py`, CEO-only, `require_ceo_role`) back the Board Programs page (Business section, `board-programs-card.tsx`): each program's live enablement, trigger kind, scope, opted-in project slugs, last-run timestamp, whether a cycle is currently open, and the most recent closed cycle's summary — plus a `Switch` that writes `board_program.{key}.enabled` through the generic feature-flag settings endpoint and a "Run now" button (disabled while a cycle is already open) that calls `open_program_cycle` off-schedule. `run-now` 404s on an unregistered key and 409s when the program is disabled, already has an open cycle, or (a project-scoped program) has no opted-in project — the three collapse into one `None` result with no finer-grained distinction available to the caller.

Per-program held artifacts reuse existing queues where the shape matches — the roadmap review queue, the X post queue (Megaphone/Barfly/War Room/spotlight all land there) — rather than growing a new panel surface per program; reports (Periscope, Sentinel) and process-change items (Coroner) and the playbook curation queue (Librarian) are the only genuinely distinct surfaces.

## Related

- CLAUDE.md's "Board Program registry" entry — the condensed architectural summary.
- `docs/rag/architecture/company-layer.md` — the Strategy Engine's `idle` → Printer trigger.
- `docs/rag/architecture/x-engine.md` — the X held-draft queue every X-bound program (Megaphone/Barfly/War Room/spotlight) materializes into.
- `docs/rag/architecture/review-findings.md` — the findings ledger Pest Control reads.
- `docs/rag/roles/auditor.md` — the playbook curation queue (`approve_playbook`/`reject_playbook`/`archive_playbook`) Coroner and Librarian both feed.
- `docs/rag/roles/product-owner.md` / `docs/rag/roles/head-marketing.md` / `docs/rag/roles/auditor.md` — each role's exact `propose_*` call shape and when it fires.
