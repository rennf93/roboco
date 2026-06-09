"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useUsageSnapshot } from "@/hooks/use-usage";
import { Coins, TrendingUp, TrendingDown, Zap, Users, Sparkles } from "lucide-react";

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
  const { data: snapshot, isLoading } = useUsageSnapshot();

  const weekTrend = snapshot
    ? snapshot.cost_this_week - snapshot.cost_last_week
    : 0;
  const weekTrendPct = snapshot?.cost_last_week
    ? Math.abs(weekTrend / snapshot.cost_last_week) * 100
    : 0;
  const weekIsUp = weekTrend >= 0;

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
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-6" />
            ))}
          </div>
        ) : (
          <div className="divide-y">
            <MetricRow
              icon={<Zap className="h-4 w-4" />}
              label="Tokens today"
              value={snapshot ? fmt(snapshot.tokens_today) : "—"}
            />
            <MetricRow
              icon={<Coins className="h-4 w-4" />}
              label="Cost today"
              value={snapshot ? fmtCost(snapshot.cost_today) : "—"}
            />
            <MetricRow
              icon={
                weekIsUp ? (
                  <TrendingUp className="h-4 w-4 text-red-500" />
                ) : (
                  <TrendingDown className="h-4 w-4 text-green-500" />
                )
              }
              label="Cost this week"
              value={snapshot ? fmtCost(snapshot.cost_this_week) : "—"}
              sub={
                snapshot ? (
                  <span
                    className={
                      "text-xs " + (weekIsUp ? "text-red-500" : "text-green-500")
                    }
                  >
                    {weekIsUp ? "▲" : "▼"} {weekTrendPct.toFixed(0)}%
                  </span>
                ) : undefined
              }
            />
            <MetricRow
              icon={<Users className="h-4 w-4" />}
              label="Active sessions"
              value={snapshot ? String(snapshot.active_sessions) : "—"}
            />
            <MetricRow
              icon={<Sparkles className="h-4 w-4 text-yellow-500" />}
              label="Cache savings"
              value={snapshot ? fmtCost(snapshot.cache_savings) : "—"}
            />
            <MetricRow
              icon={<TrendingUp className="h-4 w-4 text-blue-500" />}
              label="Top consumer"
              value={snapshot?.top_consumer ?? "—"}
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
