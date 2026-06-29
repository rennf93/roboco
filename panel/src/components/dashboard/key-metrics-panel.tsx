"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
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
  },
  {
    key: "completion_rate",
    label: "Completion Rate",
    icon: <CheckCircle className="h-4 w-4" />,
    format: (v) => `${Math.round(v * 100)}%`,
  },
  {
    key: "documentation_coverage",
    label: "Documentation Coverage",
    icon: <BarChart3 className="h-4 w-4" />,
    format: (v) => `${Math.round(v * 100)}%`,
  },
  {
    key: "active_blockers",
    label: "Active Blockers",
    icon: <AlertTriangle className="h-4 w-4" />,
    format: (v) => `${v}`,
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
                <div key={m.key} className="flex items-center justify-between">
                  <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    {m.icon}
                    {m.label}
                  </div>
                  <span className="font-medium">
                    {value != null ? (m.format ? m.format(value) : value) : "-"}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
