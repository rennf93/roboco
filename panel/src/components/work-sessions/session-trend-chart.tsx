"use client";

import { useMemo } from "react";
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
import { chartTooltipStyle } from "@/components/charts/chart-tooltip";
import type { WorkSessionSummary } from "@/types";

interface SessionTrendChartProps {
  sessions: WorkSessionSummary[] | undefined;
  isLoading: boolean;
}

const HOURLY_SPAN_MS = 36 * 60 * 60 * 1000;

interface Bucket {
  key: string;
  label: string;
  count: number;
}

/**
 * Buckets session `started_at` timestamps by hour (span <= 36h) or by day
 * (wider span), mirroring the hourly/daily switch usage-time-series-chart
 * applies for its period-selected data.
 */
function bucketSessions(sessions: WorkSessionSummary[]): Bucket[] {
  if (sessions.length === 0) return [];

  const times = sessions.map((s) => new Date(s.started_at).getTime());
  const spanMs = Math.max(...times) - Math.min(...times);
  const hourly = spanMs <= HOURLY_SPAN_MS;

  const counts = new Map<string, number>();
  for (const s of sessions) {
    const d = new Date(s.started_at);
    const key = hourly
      ? `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}-${d.getHours()}`
      : `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }

  const buckets: Bucket[] = Array.from(counts.entries()).map(([key, count]) => {
    const [y, m, day, hour] = key.split("-").map(Number);
    const d = new Date(y, m, day, hour ?? 0);
    const label = hourly
      ? d.getHours().toString().padStart(2, "0") + ":00"
      : d.getMonth() + 1 + "/" + d.getDate();
    return { key, label, count };
  });

  buckets.sort((a, b) => a.key.localeCompare(b.key));
  return buckets;
}

/**
 * Session-start volume trend for the Work Sessions page. `GET /work-sessions`
 * (unfiltered, as this page calls it) returns only currently ACTIVE sessions
 * — there is no tokens/cost/duration field on WorkSessionSummary (that data
 * lives in agent_spawn_sessions, a different table) and no historical depth
 * beyond whatever is active right now. So this charts what's honestly here:
 * a start-time distribution of the active sessions already on the page,
 * labeled accordingly rather than presented as a full history.
 */
export function SessionTrendChart({
  sessions,
  isLoading,
}: SessionTrendChartProps) {
  const buckets = useMemo(() => bucketSessions(sessions ?? []), [sessions]);

  return (
    <Card>
      <CardHeader className="pb-2">
        <HelpTip label="When today's currently active work sessions began, bucketed by hour or day — this list only ever shows active sessions, not full session history">
          <CardTitle className="text-base">Active Session Starts</CardTitle>
        </HelpTip>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-52 w-full" />
        ) : buckets.length === 0 ? (
          <p className="text-sm text-muted-foreground text-center py-12">
            No active sessions
          </p>
        ) : (
          <ResponsiveContainer width="100%" height={208}>
            <BarChart
              data={buckets}
              margin={{ top: 4, right: 8, left: 0, bottom: 0 }}
            >
              <CartesianGrid strokeDasharray="3 3" className="opacity-20" />
              <XAxis
                dataKey="label"
                tick={{ fontSize: 10 }}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                allowDecimals={false}
                tick={{ fontSize: 10 }}
                axisLine={false}
                tickLine={false}
                width={28}
              />
              <Tooltip
                {...chartTooltipStyle}
                formatter={(value) => [
                  typeof value === "number" ? value : 0,
                  "Sessions started",
                ]}
              />
              <Bar
                dataKey="count"
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
