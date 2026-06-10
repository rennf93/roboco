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
import type { TeamUsageRow } from "@/types";

interface TeamUsageChartProps {
  data: TeamUsageRow[] | undefined;
  isLoading: boolean;
}

function fmtK(n: number): string {
  if (n >= 1_000) return (n / 1_000).toFixed(0) + "k";
  return String(n);
}

export function TeamUsageChart({ data, isLoading }: TeamUsageChartProps) {
  const chartData = [...(data ?? [])]
    .sort((a, b) => b.total_tokens - a.total_tokens)
    .map((row) => ({
      name: row.team.replace(/_/g, " "),
      Tokens: row.total_tokens,
    }));

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Team Tokens</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-52 w-full" />
        ) : (
          <ResponsiveContainer width="100%" height={208}>
            <BarChart
              data={chartData}
              margin={{ top: 4, right: 8, left: 0, bottom: 8 }}
            >
              <CartesianGrid strokeDasharray="3 3" className="opacity-20" />
              <XAxis
                dataKey="name"
                tick={{ fontSize: 11 }}
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
                formatter={(value) => [
                  fmtK(typeof value === "number" ? value : 0),
                  "Tokens",
                ]}
                contentStyle={{ fontSize: 12 }}
              />
              <Bar dataKey="Tokens" fill="var(--chart-2)" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}
