"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Progress } from "@/components/ui/progress";
import { HelpTip } from "@/components/ui/help-tip";
import {
  BarChart3,
  CheckCircle,
  Clock,
  FileText,
  AlertTriangle,
} from "lucide-react";

interface QualityMetricsPanelProps {
  metrics: Record<string, number> | undefined;
  isLoading: boolean;
}

interface MetricDisplay {
  key: string;
  label: string;
  icon: React.ReactNode;
  format: (value: number) => string;
  isPercent?: boolean;
  tip: string;
}

const METRICS: MetricDisplay[] = [
  {
    key: "tasks_completed_24h",
    label: "Tasks Completed (24h)",
    icon: <CheckCircle className="h-4 w-4 text-green-500" />,
    format: (v) => String(v),
    tip: "Tasks that reached completed in the last 24 hours",
  },
  {
    key: "qa_pass_rate",
    label: "QA Pass Rate",
    icon: <BarChart3 className="h-4 w-4 text-blue-500" />,
    format: (v) => `${Math.round(v * 100)}%`,
    isPercent: true,
    tip: "Share of QA reviews that passed on the first attempt, no fail bounce",
  },
  {
    key: "avg_completion_time",
    label: "Avg Completion Time",
    icon: <Clock className="h-4 w-4 text-purple-500" />,
    format: (v) =>
      `${(typeof v === "number" ? v : parseFloat(v) || 0).toFixed(1)}h`,
    tip: "Average wall-clock time from claim to completion across recent tasks",
  },
  {
    key: "documentation_rate",
    label: "Documentation Rate",
    icon: <FileText className="h-4 w-4 text-indigo-500" />,
    format: (v) => `${Math.round(v * 100)}%`,
    isPercent: true,
    tip: "Share of completed tasks that passed through a documentation step",
  },
  {
    key: "active_blockers",
    label: "Active Blockers",
    icon: <AlertTriangle className="h-4 w-4 text-red-500" />,
    format: (v) => String(v),
    tip: "Tasks currently in the blocked status right now",
  },
  {
    key: "longest_block_hours",
    label: "Longest Block",
    icon: <Clock className="h-4 w-4 text-orange-500" />,
    format: (v) => `${v}h`,
    tip: "How long the longest-running currently-blocked task has been stuck",
  },
];

export function QualityMetricsPanel({
  metrics,
  isLoading,
}: QualityMetricsPanelProps) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <HelpTip label="Rolling delivery metrics feeding the Auditor's oversight view, refreshed every 30s">
          <CardTitle className="text-lg flex items-center gap-2">
            <BarChart3 className="h-5 w-5" />
            Quality Metrics
          </CardTitle>
        </HelpTip>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-4">
            {METRICS.map((m) => (
              <Skeleton key={m.key} className="h-8" />
            ))}
          </div>
        ) : (
          <div className="space-y-4">
            {METRICS.map((m) => {
              const value = metrics?.[m.key];
              return (
                <div key={m.key}>
                  <HelpTip label={m.tip}>
                    <div className="flex items-center justify-between text-sm mb-1">
                      <div className="flex items-center gap-2 text-muted-foreground">
                        {m.icon}
                        {m.label}
                      </div>
                      <span className="font-medium">
                        {value != null ? m.format(value) : "-"}
                      </span>
                    </div>
                  </HelpTip>
                  {m.isPercent && value != null && (
                    <Progress value={value * 100} className="h-1.5" />
                  )}
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
