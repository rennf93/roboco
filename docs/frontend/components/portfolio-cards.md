# PortfolioCards component

The CEO's per-project portfolio view on the Overview page: one card per governed project showing its delivery metrics, wired into `CommandCenter` behind a client-side `CeoGate` so agent-role sessions never mount the section (or fetch the CEO-only endpoint).

> **PR:** #980 (task `8c231d27`) — **Status:** QA-passed
> **Data layer:** sibling task `56d41b9c` — `usePortfolio()` hook + `PortfolioCard` type (consumed, not redefined)
> **Backend contract:** `docs/backend/api/dashboard-portfolio.md` — `GET /api/dashboard/portfolio`

## Purpose

The dashboard previously aggregated per-team or per-agent; `PortfolioCards` renders the cross-fleet view the backend portfolio endpoint provides: per-project active task count, median lead time, rework rate, open findings, and monthly budget burn, side by side, with a drill-down to each project's task list.

## Files

| File | Role |
|------|------|
| `panel/src/components/dashboard/portfolio-cards.tsx` | `PortfolioCards` — the section: cards grid, skeleton, empty and error states. |
| `panel/src/components/dashboard/ceo-gate.tsx` | `CeoGate` — renders children only for a CEO-role session; renders nothing otherwise. |
| `panel/src/components/dashboard/panel-role.ts` | `currentPanelRole()` / `isCeoRole()` — the single seam for the session's presented role. |
| `panel/src/components/dashboard/command-center.tsx` | Wiring: the Portfolio section (title + `HelpTip`) inside `<CeoGate>`, below the cost-trend chart. |
| `panel/src/components/dashboard/__tests__/portfolio-cards.test.tsx` | 9 tests: metrics render, null lead time, link hrefs, endpoint-order DOM assertion, skeleton count, empty, error, CEO renders, agent role renders nothing (and never calls `usePortfolio`). |
| `panel/src/components/dashboard/__tests__/command-center.test.tsx` | `PortfolioCardsStub` in the stub-ordering tests + role-mocked test that the section hides for an agent role. |

## The card

Each project renders as one `Card` wrapped in a Next.js `<Link>`:

- **Header:** `project_name`.
- **Five metric rows** (`MetricRow`), each icon + label + value with a `HelpTip` tooltip:
  1. Active tasks — `active_task_count` (tasks in flight: non-backlog, non-terminal)
  2. Median lead time — `median_lead_time_hours` (30-day window; `null` renders an em dash `—`)
  3. Rework rate — `rework_rate` (share of completed tasks that passed through a revision)
  4. Open findings — `open_findings_count` (open review findings, all-time)
  5. Budget burn (mo) — `monthly_budget_burn_usd` (calendar month-to-date agent-token spend)
- **Whole-card link:** `/tasks?project=<project_slug>` (slug URI-encoded, `prefetch={false}`).

### Formatting precedent

Formatting reuses the existing dashboard-card precedents rather than new helpers:

| Metric | Format | Precedent |
|---|---|---|
| Rework rate | `Math.round(rate * 100) + "%"` | key-metrics-panel |
| Lead time | `.toFixed(1) + "h"`; `null` → `—` | scorecard-overview-panel |
| Budget burn | `"$" + .toFixed(2)` | usage-overview-panel, scorecard-overview-panel |

Metric values use `tabular-nums` (this is a data-dense surface — proportional digits would jiggle column widths). No decorative animation; the card's only transition is a `hover:bg-accent/50` affordance.

## Sorting: endpoint order, no client re-sort

`GET /api/dashboard/portfolio` returns rows sorted by `active_task_count` **descending** (most active first). `PortfolioCards` maps the array as delivered — there is deliberately no `.sort()` client-side, so the server stays the single source of ordering truth. A DOM-order test pins this (`compareDocumentPosition`).

## CEO gating

The backend `require_ceo_role`-gates the portfolio endpoint (403 for non-CEO), but before PR #980 no panel file had a client-side role check — existing CEO surfaces (release proposal card, CEO approval queue) relied purely on that backend 403. `CeoGate` introduces the minimal client-side twin:

- `panel-role.ts` `currentPanelRole()` resolves the role this panel session presents to the API — the `X-Agent-Role` the shared axios client (`lib/api/client.ts`) injects, defaulting to the human CEO (`lib/constants.ts` `CEO_ROLE`). `isCeoRole()` compares it. Both live in one module so tests mock it as a single seam: tests mock `currentPanelRole` and simulate an agent role by returning `"developer"`.
- `CeoGate` is a synchronous wrapper that returns `null` for non-CEO roles. Because it gates *before* rendering, `PortfolioCards` never mounts for an agent role and `usePortfolio()` is never called — the CEO-only endpoint is never fetched from an agent-role session (asserted in the gating tests).
- `CommandCenter` wraps the whole Portfolio section (`<CeoGate><section>…</section></CeoGate>`), so gating happens once for the section, not per card.

If a richer per-session identity lands later, `panel-role.currentPanelRole()` is the single place to rewire.

## States

| State | Render |
|---|---|
| Loading | Grid of 3 `ProjectCardSkeleton`s matching the final layout (title + 5 metric-row placeholders per card). |
| Empty | Centered muted text: "No projects in the portfolio yet." — the endpoint returns the full governed fleet, so this means no projects exist. |
| Error | Muted one-liner: "Failed to load portfolio metrics." — never an endless skeleton. |
| Data | The cards grid (`grid-cols-1 md:grid-cols-2 xl:grid-cols-3`), keyed by `project_id`. |

## Testing

```bash
cd panel
pnpm test portfolio-cards
pnpm test command-center
```

Covered behaviors:

- One card per project with all five metrics; null median lead time renders as `—`.
- Each card links to `/tasks?project=<slug>` with the exact href.
- Endpoint order preserved (DOM-position assertion, no client re-sort).
- Skeleton count matches the final layout (3 cards × 6 placeholders = 18).
- Empty and error states render instead of skeletons.
- CEO-gating both directions: section renders for CEO; for an agent role it renders nothing AND `usePortfolio` is never called.
- `CommandCenter` stub-ordering tests place Portfolio below the cost-trend chart; a role-mocked test hides it for an agent role.

## Known follow-up: drill-down param mismatch

The brief pinned the drill-down href to `/tasks?project=<project_slug>`, implemented literally — but the tasks page's `project` query param currently filters against `task.project_id` (UUIDs), not slugs. Until the tasks filter accepts slugs (or the href switches to `project_id`), a card click may filter the task list to an empty result. Tracked as a follow-up; the href matches the brief's stated contract, and the test asserts that contract. Fixing it is a one-line change in either `portfolio-cards.tsx` or `tasks/page.tsx` depending on which side wins — plus the href assertion in `portfolio-cards.test.tsx`.

## Related

- `docs/backend/api/dashboard-portfolio.md` — the endpoint this consumes (aggregations, gate semantics, field meanings).
- `panel/src/hooks/use-portfolio.ts` / `panel/src/types/index.ts` — the data-layer leaf (task `56d41b9c`).
- `panel/src/components/dashboard/command-center.tsx` — the Overview wiring and section ordering.