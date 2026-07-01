"use client";

import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useOrgScorecard } from "@/hooks/use-observability";
import {
  Trophy,
  CheckCircle2,
  Gauge,
  Clock,
  Coins,
  ArrowRight,
} from "lucide-react";

function pctOrNa(rate: number | null): string {
  return rate === null ? "n/a" : (rate * 100).toFixed(0) + "%";
}

function numOrNa(value: number | null, digits = 2): string {
  return value === null ? "n/a" : value.toFixed(digits);
}

interface MetricRowProps {
  icon: React.ReactNode;
  label: string;
  value: string;
}

function MetricRow({ icon, label, value }: MetricRowProps) {
  return (
    <div className="flex items-center justify-between py-1">
      <div className="text-muted-foreground flex items-center gap-2 text-sm">
        {icon}
        {label}
      </div>
      <span className="text-sm font-semibold">{value}</span>
    </div>
  );
}

/**
 * Dashboard overview of the org-wide performance rollup (last 30 days). Headline
 * figures from useOrgScorecard with a deep-link into the full Scorecards tab.
 */
export function ScorecardOverviewPanel() {
  const { data, isLoading } = useOrgScorecard();

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-lg">
            <Trophy className="h-5 w-5" />
            Performance
          </CardTitle>
          <Link
            href="/metrics?tab=scorecards"
            className="text-muted-foreground hover:text-foreground flex items-center gap-1 text-xs"
          >
            Scorecards
            <ArrowRight className="h-3 w-3" />
          </Link>
        </div>
      </CardHeader>
      <CardContent>
        {isLoading || !data ? (
          <div className="space-y-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-6" />
            ))}
          </div>
        ) : (
          <div className="divide-y">
            <MetricRow
              icon={<CheckCircle2 className="h-4 w-4" />}
              label="Tasks completed (30d)"
              value={String(data.tasks_completed)}
            />
            <MetricRow
              icon={<Gauge className="h-4 w-4" />}
              label="First-pass yield"
              value={pctOrNa(data.first_pass_yield)}
            />
            <MetricRow
              icon={<Gauge className="h-4 w-4 text-blue-500" />}
              label="Throughput / hr"
              value={numOrNa(data.effort_throughput_per_hour)}
            />
            <MetricRow
              icon={<Clock className="h-4 w-4" />}
              label="Active effort"
              value={data.active_runtime_hours.toFixed(1) + "h"}
            />
            <MetricRow
              icon={<Coins className="h-4 w-4" />}
              label="Cost"
              value={"$" + data.cost_usd.toFixed(2)}
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
