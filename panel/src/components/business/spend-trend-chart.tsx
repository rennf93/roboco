"use client";

import {
  BarChart,
  Bar,
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

interface SpendTrendChartProps {
  data: UsageTimePoint[] | undefined;
  isLoading: boolean;
}

function fmtCost(n: number): string {
  return "$" + n.toFixed(2);
}

/**
 * Daily-spend breakdown behind the Scorecard's "30-day spend" figure —
 * reuses GET /usage/time-series (period=30d, daily buckets), the same
 * series-shaped endpoint the Overview page's CostTrendChart draws from.
 */
export function SpendTrendChart({ data, isLoading }: SpendTrendChartProps) {
  const granularity = bucketGranularity((data ?? []).map((p) => p.bucket));
  const chartData = (data ?? []).map((p) => ({
    day: formatBucket(p.bucket, granularity),
    Spend: p.cost_usd,
  }));

  return (
    <Card>
      <CardHeader className="pb-2">
        <HelpTip label="Provider-priced agent-session cost per day over the trailing 30 days — the daily breakdown behind the 30-day spend total above">
          <CardTitle className="text-base">Daily Spend (30d)</CardTitle>
        </HelpTip>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : chartData.length === 0 ? (
          <p className="text-sm text-muted-foreground text-center py-10">
            No spend data
          </p>
        ) : (
          <ResponsiveContainer width="100%" height={160}>
            <BarChart
              data={chartData}
              margin={{ top: 4, right: 8, left: 0, bottom: 0 }}
            >
              <CartesianGrid strokeDasharray="3 3" className="opacity-20" />
              <XAxis
                dataKey="day"
                tick={{ fontSize: 9 }}
                interval="preserveStartEnd"
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
                  "Spend",
                ]}
              />
              <Bar
                dataKey="Spend"
                fill="var(--chart-3)"
                radius={[3, 3, 0, 0]}
              />
            </BarChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}
