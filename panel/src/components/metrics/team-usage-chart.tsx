"use client";

import { useState } from "react";
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
import { SegmentedControl } from "@/components/ui/segmented-control";
import { HelpTip } from "@/components/ui/help-tip";
import { useIsMobile } from "@/hooks/use-is-mobile";
import type { TeamUsageRow } from "@/types";

interface TeamUsageChartProps {
  data: TeamUsageRow[] | undefined;
  isLoading: boolean;
}

function fmtK(n: number): string {
  if (n >= 1_000) return (n / 1_000).toFixed(0) + "k";
  return String(n);
}

const VIEW_OPTIONS = [
  { value: "chart", label: "Chart" },
  { value: "table", label: "Table" },
];

export function TeamUsageChart({ data, isLoading }: TeamUsageChartProps) {
  const isMobile = useIsMobile();
  const [view, setView] = useState<"chart" | "table">("chart");
  const chartData = [...(data ?? [])]
    .sort((a, b) => b.total_tokens - a.total_tokens)
    .map((row) => ({
      name: row.team.replace(/_/g, " "),
      Tokens: row.total_tokens,
    }));
  const tableRows = [...(data ?? [])].sort(
    (a, b) => b.total_tokens - a.total_tokens,
  );

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-base">Team Tokens</CardTitle>
          <SegmentedControl
            options={VIEW_OPTIONS}
            value={view}
            onValueChange={(v) => setView(v as "chart" | "table")}
            aria-label="Team tokens view"
          />
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-52 w-full" />
        ) : view === "table" ? (
          <div className="max-h-52 overflow-y-auto">
            <table className="w-full text-sm">
              <thead className="text-muted-foreground text-xs">
                <tr>
                  <th className="text-left font-medium py-1">Team</th>
                  <th className="text-right font-medium py-1">Tokens</th>
                  <th className="text-right font-medium py-1">
                    <HelpTip label="Share of total tokens across all teams in this window">
                      <span>%</span>
                    </HelpTip>
                  </th>
                </tr>
              </thead>
              <tbody>
                {tableRows.map((row) => (
                  <tr key={row.team} className="border-t">
                    <td className="py-1 capitalize">
                      {row.team.replace(/_/g, " ")}
                    </td>
                    <td className="py-1 text-right tabular-nums">
                      {row.total_tokens.toLocaleString()}
                    </td>
                    <td className="py-1 text-right tabular-nums">
                      {row.pct_of_total.toFixed(1)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={208}>
            <BarChart
              data={chartData}
              margin={{
                top: 4,
                right: 8,
                left: 0,
                bottom: isMobile ? 24 : 8,
              }}
            >
              <CartesianGrid strokeDasharray="3 3" className="opacity-20" />
              <XAxis
                dataKey="name"
                tick={{ fontSize: isMobile ? 9 : 11 }}
                angle={isMobile ? -45 : 0}
                textAnchor={isMobile ? "end" : "middle"}
                interval={0}
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
              <Bar
                dataKey="Tokens"
                fill="var(--chart-1)"
                radius={[3, 3, 0, 0]}
              />
            </BarChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}
