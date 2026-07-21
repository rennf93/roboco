"use client";

import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { HelpTip } from "@/components/ui/help-tip";
import { formatBucket, bucketGranularity } from "@/lib/format";
import { chartTooltipStyle } from "@/components/charts/chart-tooltip";
import type { UsageTimePoint } from "@/types";

interface CostTrendChartProps {
  data: UsageTimePoint[] | undefined;
  isLoading: boolean;
}

function fmtCost(n: number): string {
  return "$" + n.toFixed(2);
}

/**
 * Compact daily spend trend for the Command Center landing page — reuses
 * GET /usage/time-series (period=7d, daily buckets) so the CEO sees where
 * the current-period totals in UsageOverviewPanel came from at a glance.
 */
export function CostTrendChart({ data, isLoading }: CostTrendChartProps) {
  const granularity = bucketGranularity((data ?? []).map((p) => p.bucket));
  const chartData = (data ?? []).map((p) => ({
    day: formatBucket(p.bucket, granularity),
    Cost: p.cost_usd,
  }));

  return (
    <Card>
      <CardHeader className="pb-2">
        <HelpTip label="Provider-priced agent-session cost per day over the last 7 days">
          <CardTitle className="text-base">Spend Trend (7d)</CardTitle>
        </HelpTip>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : chartData.length === 0 ? (
          <p className="text-sm text-muted-foreground text-center py-10">
            No usage data
          </p>
        ) : (
          <ResponsiveContainer width="100%" height={160}>
            <AreaChart
              data={chartData}
              margin={{ top: 4, right: 8, left: 0, bottom: 0 }}
            >
              <defs>
                <linearGradient id="fillCost" x1="0" y1="0" x2="0" y2="1">
                  <stop
                    offset="5%"
                    stopColor="var(--chart-3)"
                    stopOpacity={0.8}
                  />
                  <stop
                    offset="95%"
                    stopColor="var(--chart-3)"
                    stopOpacity={0.1}
                  />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" className="opacity-20" />
              <XAxis
                dataKey="day"
                tick={{ fontSize: 10 }}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                tickFormatter={fmtCost}
                tick={{ fontSize: 10 }}
                axisLine={false}
                tickLine={false}
                width={44}
              />
              <Tooltip
                {...chartTooltipStyle}
                formatter={(value) => [
                  fmtCost(typeof value === "number" ? value : 0),
                  "Cost",
                ]}
              />
              <Area
                type="monotone"
                dataKey="Cost"
                stroke="var(--chart-3)"
                fill="url(#fillCost)"
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}
