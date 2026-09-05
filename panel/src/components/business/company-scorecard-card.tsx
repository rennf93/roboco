"use client";

import { useQuery } from "@tanstack/react-query";
import { cockpitApi, type CockpitSummary } from "@/lib/api/cockpit";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { OfflineState } from "@/components/ui/offline-state";
import { HelpTip } from "@/components/ui/help-tip";
import { useUsageTimeSeries } from "@/hooks/use-usage";
import { SpendTrendChart } from "./spend-trend-chart";
import type { UsageTimePoint } from "@/types";

// ---------------------------------------------------------------------------
// Loading skeleton — three grouped skeleton blocks
// ---------------------------------------------------------------------------

function ScorecardSkeleton() {
  return (
    <Card>
      <CardHeader>
        <Skeleton className="h-5 w-40 mb-1" />
        <Skeleton className="h-4 w-64" />
      </CardHeader>
      <CardContent className="space-y-6">
        {/* Group 1: Delivery + Spend */}
        <div className="space-y-3">
          <Skeleton className="h-4 w-20" />
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Skeleton className="h-16 rounded-lg" />
            <Skeleton className="h-16 rounded-lg" />
            <Skeleton className="h-16 rounded-lg" />
            <Skeleton className="h-16 rounded-lg" />
          </div>
        </div>
        {/* Group 2: Spend */}
        <div className="space-y-3">
          <Skeleton className="h-4 w-16" />
          <Skeleton className="h-12 rounded-lg" />
        </div>
        {/* Group 3: Speed + Objectives */}
        <div className="space-y-3">
          <Skeleton className="h-4 w-16" />
          <Skeleton className="h-10 rounded-lg" />
          <Skeleton className="h-4 w-24 mt-2" />
          <Skeleton className="h-10 rounded-lg" />
          <Skeleton className="h-10 rounded-lg" />
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Section header helper
// ---------------------------------------------------------------------------

function SectionLabel({ children, ...props }: React.ComponentProps<"p">) {
  return (
    <p
      className="text-xs font-semibold uppercase tracking-wide text-muted-foreground"
      {...props}
    >
      {children}
    </p>
  );
}

// ---------------------------------------------------------------------------
// Delivery section
// ---------------------------------------------------------------------------

interface DeliveryMetricProps {
  label: string;
  value: number;
  hint: string;
}

function DeliveryMetric({ label, value, hint }: DeliveryMetricProps) {
  return (
    <HelpTip label={hint}>
      <div className="rounded-lg border bg-card p-3 text-center">
        <div className="text-2xl font-bold tabular-nums">{value}</div>
        <div className="text-xs text-muted-foreground mt-0.5">{label}</div>
      </div>
    </HelpTip>
  );
}

interface DeliverySectionProps {
  delivery: CockpitSummary["delivery"];
}

function DeliverySection({ delivery }: DeliverySectionProps) {
  return (
    <div className="space-y-2">
      <HelpTip label="Live task counts across the pipeline — only 'Done (30 d)' looks backward">
        <SectionLabel>Delivery</SectionLabel>
      </HelpTip>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <DeliveryMetric
          label="In flight"
          value={delivery.in_flight}
          hint="Tasks currently claimed or in progress"
        />
        <DeliveryMetric
          label="Blocked"
          value={delivery.blocked}
          hint="Tasks stuck on an external dependency"
        />
        <DeliveryMetric
          label="Awaiting CEO"
          value={delivery.awaiting_ceo}
          hint="Tasks escalated to you for final approval"
        />
        <DeliveryMetric
          label="Done (30 d)"
          value={delivery.completed_30d ?? 0}
          hint="Root delivery tasks completed in the last 30 days — same population as the Speed section's median lead time"
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Spend section
// ---------------------------------------------------------------------------

interface SpendSectionProps {
  spend: CockpitSummary["spend"];
  spendTrend: UsageTimePoint[] | undefined;
  spendTrendLoading: boolean;
}

function SpendSection({
  spend,
  spendTrend,
  spendTrendLoading,
}: SpendSectionProps) {
  const {
    monthly_budget_cap_usd,
    spend_30d_usd,
    projected_monthly_usd,
    over_budget,
  } = spend;

  // Red/destructive only when cap is a non-null number AND over_budget is true
  const isOverBudget = monthly_budget_cap_usd !== null && over_budget;

  return (
    <div className="space-y-2">
      <SectionLabel>Spend</SectionLabel>
      <div className="rounded-lg border p-3 space-y-1.5">
        <div className="flex items-center justify-between text-sm">
          <HelpTip label="Total agent-session cost over the trailing 30 days (Anthropic priced; local/Ollama models cost $0)">
            <span className="text-muted-foreground">30-day spend</span>
          </HelpTip>
          <span className="font-medium tabular-nums">
            ${spend_30d_usd.toFixed(2)}
          </span>
        </div>
        {projected_monthly_usd !== null && (
          <div className="flex items-center justify-between text-sm">
            <HelpTip label="The 30-day spend extrapolated to a full month">
              <span className="text-muted-foreground">Projected monthly</span>
            </HelpTip>
            <span className="font-medium tabular-nums">
              ${projected_monthly_usd.toFixed(2)}
            </span>
          </div>
        )}
        <div className="flex items-center justify-between text-sm">
          <HelpTip label="Set via operating_policy.monthly_budget_cap on the Goals tab">
            <span className="text-muted-foreground">Monthly cap</span>
          </HelpTip>
          {monthly_budget_cap_usd === null ? (
            <span className="text-muted-foreground italic">
              No budget cap set
            </span>
          ) : (
            <span
              className={
                isOverBudget
                  ? "font-semibold text-destructive tabular-nums"
                  : "font-medium tabular-nums"
              }
            >
              ${monthly_budget_cap_usd.toFixed(2)}
              {isOverBudget && (
                <span className="ml-1 text-xs">(over budget)</span>
              )}
            </span>
          )}
        </div>
      </div>
      <SpendTrendChart data={spendTrend} isLoading={spendTrendLoading} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Speed section
// ---------------------------------------------------------------------------

interface SpeedSectionProps {
  medianLeadTimeHours: number | null | undefined;
}

function SpeedSection({ medianLeadTimeHours }: SpeedSectionProps) {
  // Show 'No data yet' when null or undefined. Never render '0h'.
  const hasData = medianLeadTimeHours != null;

  return (
    <div className="space-y-2">
      <SectionLabel>Speed</SectionLabel>
      <div className="rounded-lg border p-3">
        <div className="flex items-center justify-between text-sm">
          <HelpTip label="Hours from creation to completion, over root delivery tasks completed in the last 30 days — excludes held CEO-approval drafts (X posts, video posts, release proposals), administrative tasks, and board-program exploration cycles">
            <span className="text-muted-foreground">Median lead time</span>
          </HelpTip>
          {hasData ? (
            <span className="font-medium tabular-nums">
              {medianLeadTimeHours.toFixed(1)}h median &mdash; target:
              &lt;&nbsp;24h
            </span>
          ) : (
            <span className="text-muted-foreground italic">No data yet</span>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Objectives section — three charter objective cards, each showing a live
// metric against its target.
//
// ponytail: The charter `objectives` field is free text
// (Record<string, unknown>[]) with no id/slug the backend guarantees, so the
// only stable key available is the objective's own `metric` wording. Each
// canonical metric below is paired to its owning objective by matching a
// keyword phrase against every `objectives[].metric` string — order-
// independent and unaffected by an extra objective — rather than by array
// index. The patterns use the full distinguishing phrase (not a single
// common word) so two objectives sharing a word, e.g. both mentioning
// "defect", don't collide via find() returning whichever comes first; an
// objective whose free-text wording happens to contain another canonical
// card's full phrase can still claim that card — this is a best-effort
// content match, not a guaranteed-unique key.
// ---------------------------------------------------------------------------

type CanonicalMetricKey = "first_pass_yield" | "median_lead_time" | "escaped_defects";

// Full distinguishing phrases, not single common words, to avoid the
// keyword-collision case above (e.g. /defect/i alone matches both a
// "defect triage" objective and the "escaped defects" objective).
const CANONICAL_METRIC_PATTERNS: Record<CanonicalMetricKey, RegExp> = {
  first_pass_yield: /shipped to merge|no human code edit/i,
  median_lead_time: /median lead time/i,
  escaped_defects: /escaped defect/i,
};

const OBJECTIVE_FALLBACK_LABELS: Record<CanonicalMetricKey, string> = {
  first_pass_yield: "Tasks shipped to merge with no human code edits",
  median_lead_time: "Median lead time, intake → merged",
  escaped_defects: "Critical escaped defects per release",
};

interface ObjectivesSectionProps {
  objectives: Record<string, unknown>[];
  firstPassYield: number | null | undefined;
  medianLeadTimeHours: number | null | undefined;
  escapedDefects: number | null | undefined;
}

function objectiveLabel(
  objectives: Record<string, unknown>[],
  key: CanonicalMetricKey,
): string {
  const pattern = CANONICAL_METRIC_PATTERNS[key];
  const match = objectives.find((objective) => {
    const raw = objective?.metric;
    return typeof raw === "string" && raw.length > 0 && pattern.test(raw);
  });
  const raw = match?.metric;
  return typeof raw === "string" && raw.length > 0
    ? raw
    : OBJECTIVE_FALLBACK_LABELS[key];
}

interface ObjectiveCardProps {
  label: string;
  hasData: boolean;
  formattedValue: string;
  targetText: string;
}

function ObjectiveCard({
  label,
  hasData,
  formattedValue,
  targetText,
}: ObjectiveCardProps) {
  return (
    <div data-testid="objective-card" className="rounded-lg border bg-card p-3 space-y-1">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="flex items-center justify-between text-sm">
        {hasData ? (
          <span className="font-medium tabular-nums">{formattedValue}</span>
        ) : (
          <span className="text-muted-foreground italic">No data yet</span>
        )}
        <span className="text-xs text-muted-foreground">
          target: {targetText}
        </span>
      </div>
    </div>
  );
}

function ObjectivesSection({
  objectives,
  firstPassYield,
  medianLeadTimeHours,
  escapedDefects,
}: ObjectivesSectionProps) {
  // Show 'No data yet' when a metric is null or undefined — never a fabricated
  // value. Mirrors SpeedSection's hasData guard.
  const fpyHasData = firstPassYield != null;
  const ltHasData = medianLeadTimeHours != null;
  const edHasData = escapedDefects != null;

  return (
    <div className="space-y-2">
      <SectionLabel>Objectives</SectionLabel>
      <div className="space-y-2">
        <ObjectiveCard
          label={objectiveLabel(objectives, "first_pass_yield")}
          hasData={fpyHasData}
          formattedValue={
            fpyHasData
              ? `${((firstPassYield as number) * 100).toFixed(0)}%`
              : ""
          }
          targetText="90%"
        />
        <ObjectiveCard
          label={objectiveLabel(objectives, "median_lead_time")}
          hasData={ltHasData}
          formattedValue={
            ltHasData
              ? `${(medianLeadTimeHours as number).toFixed(1)}h`
              : ""
          }
          targetText="< 24h"
        />
        <ObjectiveCard
          label={objectiveLabel(objectives, "escaped_defects")}
          hasData={edHasData}
          formattedValue={edHasData ? `${escapedDefects as number}` : ""}
          targetText="0"
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Scorecard body — rendered when data is available
// ---------------------------------------------------------------------------

interface ScorecardBodyProps {
  data: CockpitSummary;
  spendTrend: UsageTimePoint[] | undefined;
  spendTrendLoading: boolean;
}

function ScorecardBody({
  data,
  spendTrend,
  spendTrendLoading,
}: ScorecardBodyProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Company Scorecard</CardTitle>
        <CardDescription>Live performance against the charter</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <DeliverySection delivery={data.delivery} />
        <SpendSection
          spend={data.spend}
          spendTrend={spendTrend}
          spendTrendLoading={spendTrendLoading}
        />
        <SpeedSection medianLeadTimeHours={data.median_lead_time_hours} />
        <ObjectivesSection
          objectives={data.objectives}
          firstPassYield={data.first_pass_yield}
          medianLeadTimeHours={data.median_lead_time_hours}
          escapedDefects={data.escaped_defects}
        />
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Public export
// ---------------------------------------------------------------------------

export function CompanyScorecardCard() {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["cockpit-summary"],
    queryFn: cockpitApi.summary,
  });
  const { data: spendTrend, isLoading: spendTrendLoading } =
    useUsageTimeSeries("30d");

  if (isLoading) return <ScorecardSkeleton />;

  if (isError || !data) {
    return (
      <OfflineState
        title="Could not load scorecard data"
        description="The cockpit summary could not be fetched. Check the backend is running."
        onRetry={() => void refetch()}
      />
    );
  }

  return (
    <ScorecardBody
      data={data}
      spendTrend={spendTrend}
      spendTrendLoading={spendTrendLoading}
    />
  );
}
