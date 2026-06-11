"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { TrendingUp, Clock, CheckCircle, Users, BarChart3, Wifi, WifiOff, Loader2 } from "lucide-react";
import type { ConnectionState } from "@/lib/websocket/connection";

interface KeyMetricsProps {
  metrics: Record<string, unknown> | undefined;
  isLoading: boolean;
  /** Current /ws/system connection state, used to render the status badge */
  wsState: ConnectionState;
}

interface MetricItem {
  key: string;
  label: string;
  icon: React.ReactNode;
  format?: (value: number) => string;
}

const METRIC_CONFIG: MetricItem[] = [
  {
    key: "velocity_24h",
    label: "Velocity (24h)",
    icon: <TrendingUp className="h-4 w-4" />,
    format: (v) => `${v} tasks`,
  },
  {
    key: "velocity_7d",
    label: "Velocity (7d)",
    icon: <BarChart3 className="h-4 w-4" />,
    format: (v) => `${v} tasks`,
  },
  {
    key: "completion_rate",
    label: "Completion Rate",
    icon: <CheckCircle className="h-4 w-4" />,
    format: (v) => `${Math.round(v * 100)}%`,
  },
  {
    key: "avg_time_to_done",
    label: "Avg. Time to Done",
    icon: <Clock className="h-4 w-4" />,
    format: (v) => `${(typeof v === "number" ? v : 0).toFixed(1)}h`,
  },
  {
    key: "active_agents",
    label: "Active Agents",
    icon: <Users className="h-4 w-4" />,
    format: (v) => `${v}`,
  },
];

/**
 * Returns the badge variant props (className + icon + label) matching the
 * AgentStreamViewer pattern.
 */
function getConnectionBadge(wsState: ConnectionState) {
  switch (wsState) {
    case "connected":
      return {
        className: "bg-green-500 text-white",
        icon: <Wifi className="h-3 w-3 mr-1" />,
        label: "Live",
      };
    case "connecting":
    case "reconnecting":
      return {
        className: "bg-yellow-500 text-white",
        icon: <Loader2 className="h-3 w-3 mr-1 animate-spin" />,
        label: wsState === "reconnecting" ? "Reconnecting..." : "Connecting...",
      };
    case "disconnected":
    default:
      return {
        className: "bg-gray-500 text-white",
        icon: <WifiOff className="h-3 w-3 mr-1" />,
        label: "Polling",
      };
  }
}

export function KeyMetricsPanel({ metrics, isLoading, wsState }: KeyMetricsProps) {
  const badge = getConnectionBadge(wsState);

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg">Key Metrics</CardTitle>
          <Badge className={badge.className}>
            {badge.icon}
            {badge.label}
          </Badge>
        </div>
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
                  <span className="font-medium transition-all duration-300 ease-in-out">
                    {value != null
                      ? m.format
                        ? m.format(value)
                        : value
                      : "-"}
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
