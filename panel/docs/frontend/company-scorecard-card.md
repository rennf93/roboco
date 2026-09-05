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

| Canonical key | Charter objective (matched by keyword against `objectives[].metric`) | Target | Metric field | Format |
|---|---|---|---|---|
| `first_pass_yield` | Tasks shipped to merge with no human code edits | `90%` | `first_pass_yield` | percentage, `(v * 100).toFixed(0)%` |
| `median_lead_time` | Median lead time, intake → merged | `< 24h` | `median_lead_time_hours` | `{value.toFixed(1)}h` |
| `escaped_defects` | Critical escaped defects per release | `0` | `escaped_defects` | count, `${value}` |

The lead-time metric is the same value `SpeedSection` renders in the Speed section above — `ObjectivesSection` reads `data.median_lead_time_hours` a second time and presents it as an objective card alongside the other two. `SpeedSection` is intentionally kept as-is.

### Derived-contract mapping

The charter `objectives` field on `CockpitSummary` is `Record<string, unknown>[]` — free-form text from the Goals tab, each entry carrying `{metric, target, status}`, with no id/slug the backend guarantees. The three metrics above are hardcoded by contrast. The mapping is **derived by content, not position**: each canonical metric key (`first_pass_yield` / `median_lead_time` / `escaped_defects`) is paired with the objective whose `metric` string matches a distinct keyword pattern (`CANONICAL_METRIC_PATTERNS` in `company-scorecard-card.tsx` — e.g. `/lead time/i` for the lead-time card), so reordering objectives in the Goals tab or adding a fourth objective cannot desync a label from its metric card.

`objectiveLabel(objectives, key)` scans `objectives` for the first entry whose `metric` matches that key's pattern and returns its text, falling back to `OBJECTIVE_FALLBACK_LABELS[key]` (the three canonical labels above) when no objective matches or the array is empty. A fabricated label is never rendered.

### "No data yet" fallback

Each card guards its metric with a `hasData` check (`value != null`, covering both `null` and `undefined`). When the metric is absent the card renders "No data yet" in italic muted text instead of a value — mirroring the `SpeedSection` pattern. The card never fabricates a value or a label.

**Runtime reality (2026-08-31): all three objective cards currently render "No data yet" every time.** The backend populates the three metrics under the `delivery` sub-object (`roboco/services/cockpit.py:75-77`, `roboco/api/schemas/cockpit.py` `DeliverySummary`), but `ScorecardBody` passes them to `ObjectivesSection` from the TOP level of the response (`data.first_pass_yield` etc.), which `CockpitSummary` never sends. The `hasData` guard therefore always sees `undefined`. The panel-side fix (read `data.delivery.*`) is tracked by the approved pest-control item "Scorecard shows 'No data yet' forever ...".

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

Both are optional and nullable so the UI degrades cleanly when the backend returns a genuine null/empty result. `first_pass_yield` is a 0–1 fraction formatted as a percentage, matching the phone's `pctOrDash(scorecard.first_pass_yield)` convention in `tg-metrics-tab.tsx`. `median_lead_time_hours` is declared top-level in the panel type and `SpeedSection` reads it from there, but the backend only sends it under `delivery`, so `SpeedSection` is in the same "No data yet" always state as the objective cards until the panel fix above lands.

The cockpit service has shipped both fields: `first_pass_yield` is populated from the scorecard and `escaped_defects` from `ReviewFindingsRepository.escaped_defects_since` (`cockpit.py:75-77`), under the `delivery` sub-object, not the top level the card reads. Until the panel fix above lands the objective cards do NOT display live values; see "Runtime reality" above.

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

- Three objective cards render with their target values when all metrics are present (labels matched by keyword from `objectives[].metric`, values formatted as `92%` / `18.7h` / `0`, targets `90%` / `< 24h` / `0`).
- A missing `first_pass_yield` (null) and `escaped_defects` (undefined) each render "No data yet" — two fallbacks — while a present `median_lead_time_hours` still shows its value.
- The fake "Revenue growth" / "Customer retention" / "Not tracked yet" stub labels do not appear in any render path.
- Reversing the order of the three `objectives` entries still pairs each metric with its correct label (content match, not array position).
- A fourth, unrelated objective appended to the array does not mispair or drop any of the three canonical metric labels.
- An empty `objectives` array still renders the three canonical fallback labels, never a fabricated one.

The `buildSummary` test helper carries `first_pass_yield: null` and `escaped_defects: null` by default, exercising the fallback path for a genuine null/empty result rather than mirroring a not-yet-shipped production state.

## Related

- `docs/map/panel.md` — the agent-facing codebase map entry for `CompanyScorecardCard` / `ObjectivesSection`.
- `panel/src/lib/api/cockpit.ts` — `CockpitSummary` type and `cockpitApi.summary()` client.
- `panel/src/components/business/spend-trend-chart.tsx` — the 30-day spend chart embedded in `SpendSection`.
- `tg-metrics-tab.tsx` — the phone cockpit's metrics tab, whose `pctOrDash(scorecard.first_pass_yield)` convention the `first_pass_yield` percentage format matches.