"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useUsageSummary } from "@/hooks/use-usage";
import { useUsageStore } from "@/store/usage-store";
import type { ConnectionState } from "@/lib/websocket/connection";
import {
  Coins,
  TrendingUp,
  TrendingDown,
  Zap,
  Activity,
  Wifi,
  WifiOff,
  Loader2,
} from "lucide-react";

function fmt(n: number, decimals = 0): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "K";
  return n.toFixed(decimals);
}

function fmtCost(n: number): string {
  return "$" + n.toFixed(2);
}

interface MetricRowProps {
  icon: React.ReactNode;
  label: string;
  value: string;
  sub?: React.ReactNode;
}

function MetricRow({ icon, label, value, sub }: MetricRowProps) {
  return (
    <div className="flex items-center justify-between py-1">
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        {icon}
        {label}
      </div>
      <div className="flex items-center gap-1">
        <span className="font-semibold text-sm transition-all duration-300 ease-in-out">
          {value}
        </span>
        {sub}
      </div>
    </div>
  );
}

/**
 * Badge props mirroring the AgentStreamViewer connection-status pattern:
 * green/Live when the /ws/system stream is connected, yellow while
 * (re)connecting, gray/Polling when it is down and the panel uses HTTP polling.
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

export function UsageOverviewPanel() {
  // The polling summary always runs in the background: it is the fallback when
  // the WS is down, and it supplies the trend (which the live snapshot, being a
  // point-in-time aggregate, does not compute).
  const { data: summary, isLoading } = useUsageSummary("24h");

  // Live token/cost pushed over /ws/system (USAGE_SNAPSHOT). Prefer it whenever
  // the stream is connected; `live` is non-null only then, so it narrows safely.
  const { wsState, usageData } = useUsageStore();
  const live = wsState === "connected" ? usageData : null;

  const tokensInput = live ? live.tokens_input : summary?.tokens_input;
  const tokensOutput = live ? live.tokens_output : summary?.tokens_output;
  const totalCost = live ? live.total_cost_usd : summary?.total_cost_usd;
  const periodLabel = live ? live.period : summary?.period;

  // Trend always comes from the polling summary (needs a prior-period baseline).
  const trendPct = summary?.trend_pct ?? 0;
  const trendUp = trendPct >= 0;

  const badge = getConnectionBadge(wsState);
  // Only show skeletons on first load with no data from either source.
  const showSkeleton = isLoading && !live;

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg flex items-center gap-2">
            <Coins className="h-5 w-5" />
            Token Usage &amp; Cost
          </CardTitle>
          <Badge className={badge.className}>
            {badge.icon}
            {badge.label}
          </Badge>
        </div>
      </CardHeader>
      <CardContent>
        {showSkeleton ? (
          <div className="space-y-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-6" />
            ))}
          </div>
        ) : (
          <div className="divide-y">
            <MetricRow
              icon={<Zap className="h-4 w-4" />}
              label="Tokens (input)"
              value={tokensInput != null ? fmt(tokensInput) : "—"}
            />
            <MetricRow
              icon={<Zap className="h-4 w-4 text-muted-foreground" />}
              label="Tokens (output)"
              value={tokensOutput != null ? fmt(tokensOutput) : "—"}
            />
            <MetricRow
              icon={<Coins className="h-4 w-4" />}
              label="Total cost"
              value={totalCost != null ? fmtCost(totalCost) : "—"}
            />
            <MetricRow
              icon={
                trendUp ? (
                  <TrendingUp className="h-4 w-4 text-red-500" />
                ) : (
                  <TrendingDown className="h-4 w-4 text-green-500" />
                )
              }
              label="Trend vs prior period"
              value={summary ? (trendUp ? "+" : "") + trendPct.toFixed(1) + "%" : "—"}
              sub={
                summary ? (
                  <span className={"text-xs " + (trendUp ? "text-red-500" : "text-green-500")}>
                    {trendUp ? "▲" : "▼"}
                  </span>
                ) : undefined
              }
            />
            <MetricRow
              icon={<Activity className="h-4 w-4 text-blue-500" />}
              label="Period"
              value={periodLabel ?? "—"}
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
