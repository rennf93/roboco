# Company Layer (Goals, Pitches, Strategy)

The **company layer** sits above day-to-day delivery: the CEO's charter, the pitch pipeline, and a background strategy watcher. The charter is always available (empty until set); the research, provisioning, and strategy-engine pieces are **opt-in and default-off** — the org runs fine without any of them.

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

The Strategy Engine is a **notify-only** background watcher (`ROBOCO_STRATEGY_ENGINE_ENABLED`, default off). Each cycle it `assess()`es the company against its standing goals and emits `StrategyObservation`s — each a `kind`, a `summary`, and a `detail` — for example `idle` (capacity sitting unused) or `stranded_blocked` (work stuck in `blocked`). It only **observes and surfaces**; it never acts on its own.

Those observations are the "needs your attention" signals shown on the Dashboard, served by `GET /api/cockpit/signals`.

## The Secretary

The CEO's chief-of-staff reads this layer (`read_company_state` returns the charter, task counts, pending pitches, and any directives awaiting confirmation) and acts on it via gated directives. See `docs/rag/roles/secretary.md`.

## Feature Toggles

| Env | Default | Enables |
|-----|---------|---------|
| `ROBOCO_RESEARCH_ENABLED` | off | Board / PM web research |
| `ROBOCO_STRATEGY_ENGINE_ENABLED` | off | The strategy watcher loop |
| `ROBOCO_PROVISIONING_ENABLED` | off | Pitch → auto-provisioned repos |

All are additive: with every toggle off, the company layer is just the charter plus the pitch record.
