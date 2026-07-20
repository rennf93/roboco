"use client";

import {
  PieChart,
  Pie,
  Cell,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { HelpTip } from "@/components/ui/help-tip";
import { chartTooltipStyle } from "@/components/charts/chart-tooltip";

interface TaskStatusSlice {
  name: string;
  value: number;
}

interface TaskStatusChartProps {
  slices: TaskStatusSlice[];
  isLoading?: boolean;
}

const CHART_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
];

/**
 * Task-status distribution donut for the Performance landing tab — the one
 * chart that tab was missing (W9-2). Fed by the status counts already
 * computed on the page; no extra hook.
 */
export function TaskStatusChart({ slices, isLoading }: TaskStatusChartProps) {
  const chartData = slices.filter((s) => s.value > 0);
  const total = chartData.reduce((sum, s) => sum + s.value, 0);

  return (
    <Card>
      <CardHeader className="pb-2">
        <HelpTip label="Live snapshot of how many tasks currently sit in each lifecycle status">
          <CardTitle className="text-base">Task Status Distribution</CardTitle>
        </HelpTip>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-52 w-full" />
        ) : total === 0 ? (
          <p className="text-sm text-muted-foreground text-center py-12">
            No tasks
          </p>
        ) : (
          <ResponsiveContainer width="100%" height={208}>
            <PieChart>
              <Pie
                data={chartData}
                cx="50%"
                cy="50%"
                innerRadius={52}
                outerRadius={80}
                dataKey="value"
                paddingAngle={3}
              >
                {chartData.map((_, idx) => (
                  <Cell
                    key={idx}
                    fill={CHART_COLORS[idx % CHART_COLORS.length]}
                  />
                ))}
              </Pie>
              <Tooltip
                {...chartTooltipStyle}
                formatter={(value, name) => [
                  `${typeof value === "number" ? value : 0} tasks`,
                  name,
                ]}
              />
              <Legend wrapperStyle={{ fontSize: 11 }} />
            </PieChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}
