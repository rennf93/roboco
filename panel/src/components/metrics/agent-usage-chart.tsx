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
import type { AgentUsageRow } from "@/types";

interface AgentUsageChartProps {
  data: AgentUsageRow[] | undefined;
  isLoading: boolean;
}

function fmtK(n: number): string {
  if (n >= 1_000) return (n / 1_000).toFixed(0) + "k";
  return String(n);
}

export function AgentUsageChart({ data, isLoading }: AgentUsageChartProps) {
  const chartData = [...(data ?? [])]
    .sort((a, b) => b.tokens_today - a.tokens_today)
    .slice(0, 10)
    .map((row) => ({
      name: row.agent_name,
      Tokens: row.tokens_today,
    }));

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Agent Tokens Today</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-52 w-full" />
        ) : (
          <ResponsiveContainer width="100%" height={208}>
            <BarChart
              data={chartData}
              margin={{ top: 4, right: 8, left: 0, bottom: 24 }}
            >
              <CartesianGrid strokeDasharray="3 3" className="opacity-20" />
              <XAxis
                dataKey="name"
                tick={{ fontSize: 10 }}
                angle={-30}
                textAnchor="end"
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
              <Bar dataKey="Tokens" fill="var(--chart-1)" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}
