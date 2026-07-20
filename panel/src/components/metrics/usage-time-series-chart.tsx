"use client";

import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { HelpTip } from "@/components/ui/help-tip";
import { useIsMobile } from "@/hooks/use-is-mobile";
import { formatTokens, formatBucket } from "@/lib/format";
import { chartTooltipStyle } from "@/components/charts/chart-tooltip";
import type { UsageTimePoint } from "@/types";

interface UsageTimeSeriesChartProps {
  data: UsageTimePoint[] | undefined;
  isLoading: boolean;
}

export function UsageTimeSeriesChart({
  data,
  isLoading,
}: UsageTimeSeriesChartProps) {
  const isMobile = useIsMobile();
  const chartData = (data ?? []).map((p) => ({
    hour: formatBucket(p.bucket),
    Input: p.tokens_input,
    Output: p.tokens_output,
  }));

  return (
    <Card>
      <CardHeader className="pb-2">
        <HelpTip label="Input (prompt) vs. output (completion) tokens per time bucket — hourly or daily, depending on the selected window">
          <CardTitle className="text-base">Token Usage Over Time</CardTitle>
        </HelpTip>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-52 w-full" />
        ) : chartData.length === 0 ? (
          <p className="text-sm text-muted-foreground py-16 text-center">
            No usage recorded in this window yet.
          </p>
        ) : (
          <ResponsiveContainer width="100%" height={208}>
            <AreaChart
              data={chartData}
              margin={{ top: 4, right: 8, left: 0, bottom: 0 }}
            >
              <defs>
                <linearGradient id="fillInput" x1="0" y1="0" x2="0" y2="1">
                  <stop
                    offset="5%"
                    stopColor="var(--chart-1)"
                    stopOpacity={0.8}
                  />
                  <stop
                    offset="95%"
                    stopColor="var(--chart-1)"
                    stopOpacity={0.1}
                  />
                </linearGradient>
                <linearGradient id="fillOutput" x1="0" y1="0" x2="0" y2="1">
                  <stop
                    offset="5%"
                    stopColor="var(--chart-2)"
                    stopOpacity={0.8}
                  />
                  <stop
                    offset="95%"
                    stopColor="var(--chart-2)"
                    stopOpacity={0.1}
                  />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" className="opacity-20" />
              <XAxis
                dataKey="hour"
                tick={{ fontSize: isMobile ? 9 : 10 }}
                interval="preserveStartEnd"
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                tickFormatter={formatTokens}
                tick={{ fontSize: 10 }}
                axisLine={false}
                tickLine={false}
                width={46}
              />
              <Tooltip
                {...chartTooltipStyle}
                formatter={(value, name) => [
                  formatTokens(typeof value === "number" ? value : 0),
                  name,
                ]}
              />
              <Legend wrapperStyle={{ fontSize: isMobile ? 10 : 12 }} />
              <Area
                type="monotone"
                dataKey="Input"
                stackId="1"
                stroke="var(--chart-1)"
                fill="url(#fillInput)"
              />
              <Area
                type="monotone"
                dataKey="Output"
                stackId="1"
                stroke="var(--chart-2)"
                fill="url(#fillOutput)"
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}
