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
import { useIsMobile } from "@/hooks/use-is-mobile";
import { chartTooltipStyle } from "@/components/charts/chart-tooltip";
import type { ModelUsageSlice } from "@/types";

// Design-system chart tokens — resolves to theme-aware palette
const CHART_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
];

interface ModelUsageDonutProps {
  data: ModelUsageSlice[] | undefined;
  isLoading: boolean;
}

export function ModelUsageDonut({ data, isLoading }: ModelUsageDonutProps) {
  const isMobile = useIsMobile();
  const chartData = (data ?? []).map((s) => ({
    name: s.model,
    value: s.total_tokens,
    cost: s.cost_usd,
    pct: s.pct_of_total,
  }));

  return (
    <Card>
      <CardHeader className="pb-2">
        <HelpTip label="Share of total tokens consumed per model in the selected window — hover a slice for exact tokens and cost">
          <CardTitle className="text-base">By Model</CardTitle>
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
            <PieChart>
              <Pie
                data={chartData}
                cx="50%"
                cy="50%"
                innerRadius={isMobile ? 44 : 52}
                outerRadius={isMobile ? 68 : 80}
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
                  (typeof value === "number" ? value : 0).toLocaleString() +
                    " tokens",
                  name,
                ]}
              />
              <Legend wrapperStyle={{ fontSize: isMobile ? 9 : 11 }} />
            </PieChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}
