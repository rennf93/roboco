"use client";

import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { HelpTip } from "@/components/ui/help-tip";
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
  tip: string;
}

function MetricRow({ icon, label, value, tip }: MetricRowProps) {
  return (
    <HelpTip label={tip}>
      <div className="flex items-center justify-between py-1">
        <div className="text-muted-foreground flex items-center gap-2 text-sm">
          {icon}
          {label}
        </div>
        <span className="text-sm font-semibold">{value}</span>
      </div>
    </HelpTip>
  );
}

/**
 * Dashboard overview of the org-wide performance rollup (last 30 days). Headline
 * figures from useOrgScorecard with a deep-link into the full Scorecards tab.
 */
export function ScorecardOverviewPanel() {
  const { data, isLoading, isError } = useOrgScorecard();

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <HelpTip label="Org-wide performance rollup for the last 30 days">
            <CardTitle className="flex items-center gap-2 text-lg">
              <Trophy className="h-5 w-5" />
              Performance
            </CardTitle>
          </HelpTip>
          <Link
            prefetch={false}
            href="/metrics?tab=scorecards"
            className="text-muted-foreground hover:text-foreground flex items-center gap-1 text-xs"
          >
            <HelpTip label="Per-agent and per-team scorecards, full detail">
              <span className="flex items-center gap-1">
                Scorecards
                <ArrowRight className="h-3 w-3" />
              </span>
            </HelpTip>
          </Link>
        </div>
      </CardHeader>
      <CardContent>
        {isError ? (
          <div className="text-muted-foreground text-sm">
            Failed to load performance metrics.
          </div>
        ) : isLoading || !data ? (
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
              tip="Tasks that reached completed org-wide in the last 30 days"
            />
            <MetricRow
              icon={<Gauge className="h-4 w-4" />}
              label="First-pass yield"
              value={pctOrNa(data.first_pass_yield)}
              tip="Share of completed tasks that shipped without a QA fail, PR-gate fail, PM reject, or CEO reject bounce"
            />
            <MetricRow
              icon={<Gauge className="h-4 w-4 text-blue-500" />}
              label="Throughput / hr"
              value={numOrNa(data.effort_throughput_per_hour)}
              tip="Tasks completed per hour of active (non-idle) agent runtime"
            />
            <MetricRow
              icon={<Clock className="h-4 w-4" />}
              label="Active effort"
              value={data.active_runtime_hours.toFixed(1) + "h"}
              tip="Total hours agents spent actively working — idle/waiting time excluded"
            />
            <MetricRow
              icon={<Coins className="h-4 w-4" />}
              label="Cost"
              value={"$" + data.cost_usd.toFixed(2)}
              tip="Total provider-priced token cost across all sessions in the period"
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
