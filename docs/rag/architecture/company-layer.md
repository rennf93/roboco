# Company Layer (Goals, Pitches, Strategy)

The **company layer** sits above day-to-day delivery: the CEO's charter, the pitch pipeline, and a background strategy watcher. The charter is always available (empty until set); research and provisioning ship default-**on** (but degrade gracefully until configured), while the strategy engine and every Board Program are **opt-in and default-off** — the org runs fine without any of them.

## The Charter (Company Goals)

A single, CEO-owned charter is the company's north star. It has four parts:

| Field | Meaning |
|-------|---------|
| `north_star` | The one outcome everything serves |
| `objectives` | Prioritized goals beneath the north star |
| `constraints` | Hard limits the company must respect |
| `operating_policy` | Operating rules (e.g. a monthly budget cap) |

The charter is a **singleton**, and it is injected — compactly — into **every agent's briefing**, so all work is goal-aware without anyone fetching it.

- `GET /api/company-goals` — read (any agent)
- `PUT /api/company-goals` — write (CEO only)

It is empty until the CEO sets it; an empty charter simply contributes nothing to briefings.

## Pitches

A **pitch** is a proposal for new product work. Its lifecycle is small:

| Status | Meaning |
|--------|---------|
| `proposed` | Submitted, awaiting the CEO |
| `provisioned` | Approved — turned into a product / project(s) |
| `rejected` | Declined |

When a pitch is approved and **provisioning is enabled** (`ROBOCO_PROVISIONING_ENABLED` plus a GitHub token and org), it can auto-create the product and its repositories (recorded in `provisioned_product_id` / `provisioned_project_ids`). With provisioning off, approval just records the decision.

## Strategy Engine

The Strategy Engine is a **notify-only** background watcher (`ROBOCO_STRATEGY_ENGINE_ENABLED`, default off). Each cycle it `assess()`es the company against its standing goals and emits `StrategyObservation`s — each a `kind`, a `summary`, and a `detail` — for example `idle` (capacity sitting unused) or `stranded_blocked` (work stuck in `blocked`). Mostly it only **observes and surfaces**, but `idle` is now also a Board Program trigger: `run_cycle` calls `BoardProgramEngine.open_program_cycle("roadmap")` (enabled + dedup checked there, so a still-open cycle is a harmless no-op) and folds the outcome into the CEO notification instead of only describing the drift. `stranded_blocked` stays notify-only — Coroner is triggered by its own dedicated event hooks (a bounce past `revision_count>=3`, a cancel-after-work-started, or a budget block; see `docs/rag/architecture/board-programs.md`), not by this engine.

Those observations are the "needs your attention" signals shown on the Dashboard, served by `GET /api/cockpit/signals`.

## Board Program Registry (Printer and thirteen siblings)

The **roadmap engine** described here is now `roadmap` ("Printer") — one entry in a fourteen-program registry (`roboco/foundation/policy/board_programs.py`) that also covers Pest Control, Spackle, Scales, Dogfood (Product Owner), Periscope, Megaphone, Mirror, Barfly, War Room, x_feature spotlight (Head of Marketing), and Coroner, Librarian, Sentinel (Auditor). See `docs/rag/architecture/board-programs.md` for the full catalog and each program's exact verb call shape; this section keeps Printer's own mechanics as the worked example, since every cron/metric program follows the identical shape.

Printer is a weekly counterpart to the pitch pipeline: instead of a one-off product proposal, the Product Owner explores the company's projects, charter, recent releases, and metrics, then proposes one themed **cycle** of 3-7 roadmap item drafts.

Mechanically it mirrors the release manager's "detect → originate a CEO-gated artifact → hold" shape:

1. Weekly (or off-schedule — see the Strategy Engine's `idle` trigger below), `BoardProgramEngine` opens ONE held, PENDING exploration task assigned to the Product Owner (`source=board_roadmap`, `confirmed_by_human=False`) via `RoadmapEngine.run_cycle()` — only when no cycle is already open (`board_program_cycles` dedup ledger).
2. The board dispatcher one-shot-spawns the Product Owner for it, who explores and calls `propose_roadmap(cycle_goal, items)` **exactly once**, persisting the goal + item drafts as a marker on the task (no dedicated table).
3. The CEO reviews the authored cycle in the roadmap queue and approves or rejects each item **individually** (`GET /api/roadmap/cycles`, `POST /api/roadmap/cycles/{task_id}/items/{item_id}/{approve,reject}`, CEO-only).
4. An approved item materializes as a real BACKLOG task (`source=roadmap`) via the same `create_task_from_draft` path pitches use — nothing here auto-starts it; normal PM activation takes it from there. The exploration task itself completes once every item is terminal (approved or rejected).
5. The CEO's per-item decisions accrue onto the cycle's LEARN ledger row (`board_program_cycles.decisions`) and feed forward into the NEXT roadmap-exploration prompt ("last cycle you proposed X, the CEO rejected it because Y") — Printer is no longer amnesiac.

Like every Board Program, it never authors work outside this held/approved chain, and it never starts anything itself.

## The Secretary

The CEO's chief-of-staff reads this layer (`read_company_state` returns the charter, task counts, pending pitches, and any directives awaiting confirmation) and acts on it via gated directives. See `docs/rag/roles/secretary.md`.

## Feature Toggles

| Env | Default | Enables |
|-----|---------|---------|
| `ROBOCO_RESEARCH_ENABLED` | **on** | Board / PM web research |
| `ROBOCO_STRATEGY_ENGINE_ENABLED` | off | The strategy watcher loop |
| `ROBOCO_PROVISIONING_ENABLED` | on (inert without a token/org) | Pitch → auto-provisioned repos |
| `ROBOCO_ROADMAP_ENGINE_ENABLED` | off | Legacy alias arming Printer only — every other Board Program is settings-store-only, no env flag (see `docs/rag/architecture/board-programs.md`) |

With every toggle off, the company layer is just the charter plus the pitch record — research and provisioning ship on by default but degrade gracefully (no key/token configured) rather than doing anything until set up.
