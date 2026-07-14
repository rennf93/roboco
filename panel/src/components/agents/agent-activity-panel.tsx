"use client";

import { useMemo } from "react";
import { formatDistanceToNow } from "date-fns";
import {
  Area,
  AreaChart,
  ResponsiveContainer,
  Tooltip as RTooltip,
} from "recharts";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { GitBranch, BookOpen } from "lucide-react";
import { useUsageTimeSeries } from "@/hooks/use-usage";
import { useWorkSessions } from "@/hooks/use-work-sessions";
import { useAgentJournalEntries } from "@/hooks/use-journals";
import { WorkSessionStatus, type UsageTimePoint } from "@/types";

interface AgentActivityPanelProps {
  agentSlug: string;
  // WorkSessionTable.agent_id is a UUID FK to agents.id, so work-sessions are
  // filtered by the agent UUID — NOT the slug. Journals take the slug.
  agentUuid?: string;
}

const SESSION_STATUS_LABEL: Record<WorkSessionStatus, string> = {
  [WorkSessionStatus.ACTIVE]: "In progress",
  [WorkSessionStatus.COMPLETED]: "Completed",
  [WorkSessionStatus.ABANDONED]: "Abandoned",
};

function fmtK(n: number): string {
  if (n >= 1_000) return (n / 1_000).toFixed(0) + "k";
  return String(n);
}

type TimelineItem = {
  kind: "session" | "journal";
  title: string;
  subtitle: string;
  timestamp: string;
};

function mergeTimeline(
  sessions: { task_id: string; branch_name: string; status: WorkSessionStatus; started_at: string }[],
  journals: { title: string; type: string; timestamp: string; task_id: string | null }[],
): TimelineItem[] {
  const items: TimelineItem[] = [
    ...sessions.map((s) => ({
      kind: "session" as const,
      title: `Task ${s.task_id.slice(0, 8)}`,
      subtitle: `${s.branch_name} · ${SESSION_STATUS_LABEL[s.status] ?? s.status}`,
      timestamp: s.started_at,
    })),
    ...journals.map((j) => ({
      kind: "journal" as const,
      title: j.title,
      subtitle: j.task_id ? `Task ${j.task_id.slice(0, 8)} · ${j.type}` : j.type,
      timestamp: j.timestamp,
    })),
  ];
  return items
    .sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime())
    .slice(0, 8);
}

export function AgentActivityPanel({
  agentSlug,
  agentUuid,
}: AgentActivityPanelProps) {
  const { data: series, isLoading: seriesLoading } = useUsageTimeSeries(
    "7d",
    agentSlug,
  );
  const { data: sessions, isLoading: sessionsLoading } = useWorkSessions(
    agentUuid ? { agent_id: agentUuid } : undefined,
  );
  const { data: journals, isLoading: journalsLoading } = useAgentJournalEntries(
    agentSlug,
    { limit: 5 },
  );

  const timeline = useMemo(
    () => mergeTimeline(sessions ?? [], journals ?? []),
    [sessions, journals],
  );
  const timelineLoading = sessionsLoading || journalsLoading;
  const hasTokens = (series ?? []).some((p: UsageTimePoint) => p.total_tokens > 0);

  return (
    <div className="grid gap-4 md:grid-cols-2">
      {/* Token activity sparkline */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Token Activity</CardTitle>
          <CardDescription>Last 7 days</CardDescription>
        </CardHeader>
        <CardContent>
          {seriesLoading ? (
            <Skeleton className="h-20 w-full" />
          ) : hasTokens ? (
            <ResponsiveContainer width="100%" height={80}>
              <AreaChart
                data={series}
                margin={{ top: 4, right: 0, left: 0, bottom: 0 }}
              >
                <defs>
                  <linearGradient id="agentSpark" x1="0" y1="0" x2="0" y2="1">
                    <stop
                      offset="5%"
                      stopColor="var(--chart-1)"
                      stopOpacity={0.3}
                    />
                    <stop
                      offset="95%"
                      stopColor="var(--chart-1)"
                      stopOpacity={0}
                    />
                  </linearGradient>
                </defs>
                <RTooltip
                  formatter={(value) => [
                    fmtK(typeof value === "number" ? value : 0),
                    "Tokens",
                  ]}
                  contentStyle={{ fontSize: 12 }}
                  labelFormatter={() => ""}
                />
                <Area
                  type="monotone"
                  dataKey="total_tokens"
                  stroke="var(--chart-1)"
                  strokeWidth={2}
                  fill="url(#agentSpark)"
                  isAnimationActive={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-muted-foreground text-sm py-6 text-center">
              No token usage in the last 7 days
            </p>
          )}
        </CardContent>
      </Card>

      {/* Activity timeline */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Recent Activity</CardTitle>
          <CardDescription>Work sessions &amp; journal entries</CardDescription>
        </CardHeader>
        <CardContent>
          {timelineLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-8 w-full" />
              ))}
            </div>
          ) : timeline.length === 0 ? (
            <p className="text-muted-foreground text-sm py-6 text-center">
              No recent activity
            </p>
          ) : (
            <ol className="space-y-3">
              {timeline.map((item, i) => (
                <li key={`${item.kind}-${i}`} className="flex gap-3">
                  <div className="mt-0.5 shrink-0">
                    {item.kind === "session" ? (
                      <GitBranch className="h-4 w-4 text-muted-foreground" />
                    ) : (
                      <BookOpen className="h-4 w-4 text-muted-foreground" />
                    )}
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium truncate">{item.title}</p>
                    <p className="text-muted-foreground text-xs truncate">
                      {item.subtitle}
                    </p>
                  </div>
                  <span className="text-muted-foreground text-xs whitespace-nowrap">
                    {formatDistanceToNow(new Date(item.timestamp), {
                      addSuffix: true,
                    })}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </CardContent>
      </Card>
    </div>
  );
}