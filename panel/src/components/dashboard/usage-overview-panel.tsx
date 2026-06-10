"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useUsageSummary } from "@/hooks/use-usage";
import { Coins, TrendingUp, TrendingDown, Zap, Activity } from "lucide-react";

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
        <span className="font-semibold text-sm">{value}</span>
        {sub}
      </div>
    </div>
  );
}

export function UsageOverviewPanel() {
  const { data: summary, isLoading } = useUsageSummary("24h");

  const trendUp = (summary?.trend_pct ?? 0) >= 0;

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-lg flex items-center gap-2">
          <Coins className="h-5 w-5" />
          Token Usage &amp; Cost
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
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
              value={summary ? fmt(summary.tokens_input) : "—"}
            />
            <MetricRow
              icon={<Zap className="h-4 w-4 text-muted-foreground" />}
              label="Tokens (output)"
              value={summary ? fmt(summary.tokens_output) : "—"}
            />
            <MetricRow
              icon={<Coins className="h-4 w-4" />}
              label="Total cost"
              value={summary ? fmtCost(summary.total_cost_usd) : "—"}
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
              value={summary ? (trendUp ? "+" : "") + summary.trend_pct.toFixed(1) + "%" : "—"}
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
              value={summary?.period ?? "—"}
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
