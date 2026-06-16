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
import type { UsageTimePoint } from "@/types";

interface UsageTimeSeriesChartProps {
  data: UsageTimePoint[] | undefined;
  isLoading: boolean;
}

function formatBucket(bucket: string): string {
  const d = new Date(bucket);
  // If the bucket has a non-zero time component it is an hourly bucket → show HH:00.
  // Otherwise it is a daily bucket → show MM/DD.
  const isHourly = d.getMinutes() === 0 && (d.getHours() !== 0 || bucket.includes("T"));
  if (isHourly && d.getSeconds() === 0 && !bucket.endsWith("T00:00:00.000Z")) {
    return d.getHours().toString().padStart(2, "0") + ":00";
  }
  return (d.getMonth() + 1) + "/" + d.getDate();
}

function fmtK(n: number): string {
  if (n >= 1_000) return (n / 1_000).toFixed(0) + "k";
  return String(n);
}

export function UsageTimeSeriesChart({ data, isLoading }: UsageTimeSeriesChartProps) {
  const chartData = (data ?? []).map((p) => ({
    hour: formatBucket(p.bucket),
    Input: p.tokens_input,
    Output: p.tokens_output,
  }));

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Token Usage Over Time</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-52 w-full" />
        ) : (
          <ResponsiveContainer width="100%" height={208}>
            <AreaChart
              data={chartData}
              margin={{ top: 4, right: 8, left: 0, bottom: 0 }}
            >
              <defs>
                <linearGradient id="fillInput" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.8} />
                  <stop offset="95%" stopColor="#3b82f6" stopOpacity={0.1} />
                </linearGradient>
                <linearGradient id="fillOutput" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.8} />
                  <stop offset="95%" stopColor="#f59e0b" stopOpacity={0.1} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" className="opacity-20" />
              <XAxis
                dataKey="hour"
                tick={{ fontSize: 10 }}
                interval={3}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                tickFormatter={fmtK}
                tick={{ fontSize: 10 }}
                axisLine={false}
                tickLine={false}
                width={36}
              />
              <Tooltip
                formatter={(value, name) => [
                  fmtK(typeof value === "number" ? value : 0),
                  name,
                ]}
                contentStyle={{ fontSize: 12 }}
              />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Area
                type="monotone"
                dataKey="Input"
                stackId="1"
                stroke="#3b82f6"
                fill="url(#fillInput)"
              />
              <Area
                type="monotone"
                dataKey="Output"
                stackId="1"
                stroke="#f59e0b"
                fill="url(#fillOutput)"
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}
