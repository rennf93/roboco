"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { HelpTip } from "@/components/ui/help-tip";
import {
  TrendingUp,
  CheckCircle,
  BarChart3,
  AlertTriangle,
} from "lucide-react";

interface KeyMetricsProps {
  metrics: Record<string, unknown> | undefined;
  isLoading: boolean;
}

interface MetricItem {
  key: string;
  label: string;
  icon: React.ReactNode;
  format?: (value: number) => string;
  tip: string;
}

// Keys must match DashboardService.get_key_metrics() — the shape /dashboard/ceo
// returns. (velocity_weekly + completion_rate + documentation_coverage are the
// 7-day rollups; active_blockers is the live blocked-task count.)
const METRIC_CONFIG: MetricItem[] = [
  {
    key: "velocity_weekly",
    label: "Velocity (7d)",
    icon: <TrendingUp className="h-4 w-4" />,
    format: (v) => `${v} tasks`,
    tip: "Tasks completed in the last 7 days",
  },
  {
    key: "completion_rate",
    label: "Completion Rate",
    icon: <CheckCircle className="h-4 w-4" />,
    format: (v) => `${Math.round(v * 100)}%`,
    tip: "Share of tasks started in the last 7 days that reached completed",
  },
  {
    key: "documentation_coverage",
    label: "Documentation Coverage",
    icon: <BarChart3 className="h-4 w-4" />,
    format: (v) => `${Math.round(v * 100)}%`,
    tip: "Share of completed tasks that passed through a documentation step",
  },
  {
    key: "active_blockers",
    label: "Active Blockers",
    icon: <AlertTriangle className="h-4 w-4" />,
    format: (v) => `${v}`,
    tip: "Tasks currently in the blocked status right now",
  },
];

export function KeyMetricsPanel({ metrics, isLoading }: KeyMetricsProps) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-lg">Key Metrics</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-3">
            {METRIC_CONFIG.map((m) => (
              <Skeleton key={m.key} className="h-6" />
            ))}
          </div>
        ) : (
          <div className="space-y-3">
            {METRIC_CONFIG.map((m) => {
              const rawValue = metrics?.[m.key];
              const value = typeof rawValue === "number" ? rawValue : null;
              return (
                <HelpTip key={m.key} label={m.tip}>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      {m.icon}
                      {m.label}
                    </div>
                    <span className="font-medium">
                      {value != null ? (m.format ? m.format(value) : value) : "-"}
                    </span>
                  </div>
                </HelpTip>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
