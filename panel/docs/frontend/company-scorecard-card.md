# Company Scorecard Card

## Overview

`CompanyScorecardCard` (`panel/src/components/business/company-scorecard-card.tsx`) is the live charter-performance card on the Business page's **Scorecard** tab (`app/(dashboard)/business/page.tsx`, `?tab=scorecard`). Its subtitle reads "Live performance against the charter". It renders four sections off a single `cockpitApi.summary()` call (`GET /api/cockpit/summary`, typed by `CockpitSummary` in `panel/src/lib/api/cockpit.ts`):

1. **Delivery** — live task counts across the pipeline (`DeliverySection`).
2. **Spend** — 30-day spend, projected monthly, monthly cap, plus the `SpendTrendChart` (`SpendSection`).
3. **Speed** — median lead time, intake → merged (`SpeedSection`).
4. **Objectives** — three charter objective cards, each showing a live metric against its target (`ObjectivesSection`).

Loading, error, and empty states are handled at the card level: `ScorecardSkeleton` while the query loads, `OfflineState` with a retry button on error or missing data.

## Objectives section

`ObjectivesSection` replaced the former `StubObjectivesSection`, a placeholder that rendered fake "Revenue growth" / "Customer retention" labels with a "Not tracked yet" badge. The stub actively misrepresented charter performance; the live section renders the three real charter objectives against their live metrics.

### The three objective cards

Each card shows the objective label, the live metric value (or "No data yet"), and the target:

| Position | Charter objective (`objectives[i].metric`) | Target | Metric field | Format |
|---|---|---|---|---|
| 0 | Tasks shipped to merge with no human code edits | `90%` | `first_pass_yield` | percentage, `(v * 100).toFixed(0)%` |
| 1 | Median lead time, intake → merged | `< 24h` | `median_lead_time_hours` | `{value.toFixed(1)}h` |
| 2 | Critical escaped defects per release | `0` | `escaped_defects` | count, `${value}` |

The lead-time metric is the same value `SpeedSection` renders in the Speed section above — `ObjectivesSection` reads `data.median_lead_time_hours` a second time and presents it as an objective card alongside the other two. `SpeedSection` is intentionally kept as-is.

### Positional-by-convention mapping

The charter `objectives` field on `CockpitSummary` is `Record<string, unknown>[]` — free-form text from the Goals tab, each entry carrying `{metric, target, status}`. The three metrics above are hardcoded by contrast. The mapping is **positional by convention, not derived**: `objectives[i].metric` supplies the label for card `i`, and card `i` reads the `i`-th hardcoded metric. This holds only while the charter has exactly three objectives in the canonical order; editing the Goals tab can desync labels from metrics. The assumption is documented in a `ponytail:` code comment at the top of the `ObjectivesSection` block in `company-scorecard-card.tsx` — a stated assumption, not something discovered later.

`objectiveLabel(objectives, index)` returns `objectives[index].metric` when it is a non-empty string, falling back to `OBJECTIVE_FALLBACK_LABELS[index]` (the three canonical labels above) when the array is empty or shorter than three. A fabricated label is never rendered.

### "No data yet" fallback

Each card guards its metric with a `hasData` check (`value != null`, covering both `null` and `undefined`). When the metric is absent the card renders "No data yet" in italic muted text instead of a value — mirroring the `SpeedSection` pattern. For the two fields backed by the cockpit service (`first_pass_yield`, `escaped_defects`), the fallback triggers only on a genuine null/empty result — not on a missing field, since the backend now populates both (`cockpit.py:76-77`). The card never fabricates a value or a label.

## CockpitSummary type changes

`panel/src/lib/api/cockpit.ts` gained two optional fields on `CockpitSummary`:

```typescript
// Fraction of tasks shipped to merge with no human code edits (0–1).
// Populated by the cockpit service (scorecard.first_pass_yield); the
// UI renders 'No data yet' only on a genuine null. Formatted as a
// percentage, matching the phone's pctOrDash convention in tg-metrics-tab.tsx.
first_pass_yield?: number | null;

// Count of critical escaped defects per release, populated by the cockpit
// service (ReviewFindingsRepository.escaped_defects_since).
escaped_defects?: number | null;
```

Both are optional and nullable so the UI degrades cleanly when the backend returns a genuine null/empty result. `first_pass_yield` is a 0–1 fraction formatted as a percentage, matching the phone's `pctOrDash(scorecard.first_pass_yield)` convention in `tg-metrics-tab.tsx`. `median_lead_time_hours` is unchanged — it already existed at the top level of the type and `SpeedSection` reads it from there.

The cockpit service has shipped both fields: `first_pass_yield` is populated from the scorecard and `escaped_defects` from `ReviewFindingsRepository.escaped_defects_since` (`cockpit.py:76-77`). The two objective cards display live values; "No data yet" appears only on a genuine null/empty result.

## Card structure

```
CompanyScorecardCard
└── ScorecardBody (data, spendTrend, spendTrendLoading)
    ├── DeliverySection   (data.delivery)
    ├── SpendSection      (data.spend, spendTrend, spendTrendLoading)
    ├── SpeedSection      (data.median_lead_time_hours)
    └── ObjectivesSection (data.objectives, first_pass_yield,
                           median_lead_time_hours, escaped_defects)
        └── ObjectiveCard × 3  (label, hasData, formattedValue, targetText)
```

`ObjectiveCard` is a presentational leaf: a `rounded-lg border bg-card p-3` card (matching `DeliveryMetric`'s styling) with the label on top and a `flex justify-between` row holding the value (or "No data yet") and the `target: {targetText}` annotation.

## Tests

`panel/src/components/business/__tests__/company-scorecard-card.test.tsx` covers the Objectives section:

- Three objective cards render with their target values when all metrics are present (labels from `objectives[i].metric`, values formatted as `92%` / `18.7h` / `0`, targets `90%` / `< 24h` / `0`).
- A missing `first_pass_yield` (null) and `escaped_defects` (undefined) each render "No data yet" — two fallbacks — while a present `median_lead_time_hours` still shows its value.
- The fake "Revenue growth" / "Customer retention" / "Not tracked yet" stub labels do not appear in any render path.

The `buildSummary` test helper carries `first_pass_yield: null` and `escaped_defects: null` by default, exercising the fallback path for a genuine null/empty result rather than mirroring a not-yet-shipped production state.

## Related

- `docs/map/panel.md` — the agent-facing codebase map entry for `CompanyScorecardCard` / `ObjectivesSection`.
- `panel/src/lib/api/cockpit.ts` — `CockpitSummary` type and `cockpitApi.summary()` client.
- `panel/src/components/business/spend-trend-chart.tsx` — the 30-day spend chart embedded in `SpendSection`.
- `tg-metrics-tab.tsx` — the phone cockpit's metrics tab, whose `pctOrDash(scorecard.first_pass_yield)` convention the `first_pass_yield` percentage format matches.