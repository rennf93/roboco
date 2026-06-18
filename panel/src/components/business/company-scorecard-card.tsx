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

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
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
}

function DeliveryMetric({ label, value }: DeliveryMetricProps) {
  return (
    <div className="rounded-lg border bg-card p-3 text-center">
      <div className="text-2xl font-bold tabular-nums">{value}</div>
      <div className="text-xs text-muted-foreground mt-0.5">{label}</div>
    </div>
  );
}

interface DeliverySectionProps {
  delivery: CockpitSummary["delivery"];
}

function DeliverySection({ delivery }: DeliverySectionProps) {
  return (
    <div className="space-y-2">
      <SectionLabel>Delivery</SectionLabel>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <DeliveryMetric label="In flight" value={delivery.in_flight} />
        <DeliveryMetric label="Blocked" value={delivery.blocked} />
        <DeliveryMetric label="Awaiting CEO" value={delivery.awaiting_ceo} />
        <DeliveryMetric
          label="Done (30 d)"
          value={delivery.completed_30d ?? 0}
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
}

function SpendSection({ spend }: SpendSectionProps) {
  const { monthly_budget_cap_usd, spend_30d_usd, projected_monthly_usd, over_budget } =
    spend;

  // Red/destructive only when cap is a non-null number AND over_budget is true
  const isOverBudget = monthly_budget_cap_usd !== null && over_budget;

  return (
    <div className="space-y-2">
      <SectionLabel>Spend</SectionLabel>
      <div className="rounded-lg border p-3 space-y-1.5">
        <div className="flex items-center justify-between text-sm">
          <span className="text-muted-foreground">30-day spend</span>
          <span className="font-medium tabular-nums">
            ${spend_30d_usd.toFixed(2)}
          </span>
        </div>
        {projected_monthly_usd !== null && (
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">Projected monthly</span>
            <span className="font-medium tabular-nums">
              ${projected_monthly_usd.toFixed(2)}
            </span>
          </div>
        )}
        <div className="flex items-center justify-between text-sm">
          <span className="text-muted-foreground">Monthly cap</span>
          {monthly_budget_cap_usd === null ? (
            <span className="text-muted-foreground italic">No budget cap set</span>
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
          <span className="text-muted-foreground">Median lead time</span>
          {hasData ? (
            <span className="font-medium tabular-nums">
              {medianLeadTimeHours.toFixed(1)}h median &mdash; target: &lt;&nbsp;24h
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
// Stub objectives section
// ---------------------------------------------------------------------------

function StubObjectivesSection() {
  const stubs = [
    { id: "obj-1", label: "Revenue growth" },
    { id: "obj-2", label: "Customer retention" },
  ];

  return (
    <div className="space-y-2">
      <SectionLabel>Objectives</SectionLabel>
      <div className="space-y-2">
        {stubs.map((stub) => (
          <div
            key={stub.id}
            className="rounded-lg border border-dashed p-3 flex items-center justify-between"
          >
            <span className="text-sm text-muted-foreground">{stub.label}</span>
            <span className="text-xs text-muted-foreground italic">
              Not tracked yet
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Scorecard body — rendered when data is available
// ---------------------------------------------------------------------------

interface ScorecardBodyProps {
  data: CockpitSummary;
}

function ScorecardBody({ data }: ScorecardBodyProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Company Scorecard</CardTitle>
        <CardDescription>Live performance against the charter</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <DeliverySection delivery={data.delivery} />
        <SpendSection spend={data.spend} />
        <SpeedSection medianLeadTimeHours={data.median_lead_time_hours} />
        <StubObjectivesSection />
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

  return <ScorecardBody data={data} />;
}
