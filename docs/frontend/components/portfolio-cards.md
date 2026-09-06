# PortfolioCards component

The CEO's per-project portfolio view on the Overview page: one card per governed project showing its delivery metrics, wired into `CommandCenter` behind a client-side `CeoGate` so agent-role sessions never mount the section (or fetch the CEO-only endpoint).

> **PR:** #980 (task `8c231d27`); drill-down href fixed in PR #1002 (task `885c823f`, revision of #981) — **Status:** QA-passed
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
- **Whole-card link:** `/tasks?project=<project_id>` (URI-encoded, `prefetch={false}`) — see "Deep-link contract" below; this used to carry `project_slug`, which is why the drill-down was broken until PR #1002.

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
- Each card links to `/tasks?project=<project_id>` with the exact href (see "Deep-link contract" above).
- Endpoint order preserved (DOM-position assertion, no client re-sort).
- Skeleton count matches the final layout (3 cards × 6 placeholders = 18).
- Empty and error states render instead of skeletons.
- CEO-gating both directions: section renders for CEO; for an agent role it renders nothing AND `usePortfolio` is never called.
- `CommandCenter` stub-ordering tests place Portfolio below the cost-trend chart; a role-mocked test hides it for an agent role.
- `tasks/__tests__/page.test.tsx`'s "?project=<id> deep-link drill-down" suite pins the receiving end: the tasks page renders only the linked project's tasks when `?project` carries a project id, and every task when the param is absent.

## Deep-link contract: card href carries the project id, not the slug (fixed PR #1002)

The tasks page's own project picker (`ProjectSelect`, `panel/src/app/(dashboard)/tasks/page.tsx`) writes `p.id` values into the `?project=` query param, and the tasks page's filter compares that param against `task.project_id` (a UUID column) — never against a slug. So `ProjectCard`'s href must carry `project.project_id`, matching that contract:

```tsx
href={`/tasks?project=${encodeURIComponent(project.project_id)}`}
```

**History (F-6cfb95f6, reviewer finding on PR #981):** the original implementation above linked cards by `project.project_slug` instead — matching the literal wording of the original brief, but not the tasks page's actual filter. Because the tasks page filters on `task.project_id`, a slug never matches any task's `project_id`, so **every** portfolio card click landed on an empty task list regardless of which project was clicked (a project can legitimately have zero tasks — this was a total contract mismatch, not that). PR #1002 (task `885c823f`) fixed it by changing only the href to carry `project.project_id`; the alternative considered was teaching the tasks page to resolve a slug via its existing `useProjects()` data, rejected because the tasks page already consumes `?project=` as an id end-to-end with no new fetch needed on the link-side fix.

If you touch either side of this contract (the tasks page's project filter, or where portfolio cards build their href), keep both ends on the same identifier (id, not slug) and update both regression tests: `portfolio-cards.test.tsx`'s href-pin test and `tasks/__tests__/page.test.tsx`'s `?project=<id>` drill-down suite.

## Related

- `docs/backend/api/dashboard-portfolio.md` — the endpoint this consumes (aggregations, gate semantics, field meanings).
- `panel/src/hooks/use-portfolio.ts` / `panel/src/types/index.ts` — the data-layer leaf (task `56d41b9c`).
- `panel/src/components/dashboard/command-center.tsx`: the Overview wiring and section ordering.
