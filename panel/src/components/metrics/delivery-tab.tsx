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
import { Badge } from "@/components/ui/badge";
import { HelpTip } from "@/components/ui/help-tip";
import {
  ResponsiveTable,
  ResponsiveTableCardList,
  ResponsiveTableCard,
  ResponsiveTableCardRow,
} from "@/components/ui/responsive-table";
import {
  useCycleTime,
  useBottlenecks,
  useRework,
  useTeamScorecard,
} from "@/hooks/use-observability";
import { chartTooltipStyle } from "@/components/charts/chart-tooltip";
import type { Scorecard } from "@/types";

const CELLS = ["backend", "frontend", "ux_ui"] as const;

function label(status: string): string {
  return status
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

function fmtDuration(seconds: number): string {
  if (seconds >= 3600) return (seconds / 3600).toFixed(1) + "h";
  if (seconds >= 60) return (seconds / 60).toFixed(0) + "m";
  return seconds.toFixed(0) + "s";
}

function pct(rate: number): string {
  return (rate * 100).toFixed(1) + "%";
}

// ─── Cycle time ───────────────────────────────────────────────────────────────

function CycleTimeCard() {
  const { data, isLoading } = useCycleTime(30);
  const chartData = (data ?? []).map((s) => ({
    name: label(s.status),
    Hours: Number((s.avg_seconds / 3600).toFixed(2)),
  }));
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">
          Cycle Time by Stage (avg, 30d)
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-52 w-full" />
        ) : chartData.length === 0 ? (
          <p className="text-sm text-muted-foreground py-16 text-center">
            No completed transitions in the window yet.
          </p>
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
                tick={{ fontSize: 10 }}
                axisLine={false}
                tickLine={false}
                width={36}
                tickFormatter={(v) => v + "h"}
              />
              <Tooltip
                {...chartTooltipStyle}
                formatter={(value) => [value + "h", "Avg"]}
              />
              <Bar
                dataKey="Hours"
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

// ─── Bottlenecks ──────────────────────────────────────────────────────────────

function BottlenecksCard() {
  const { data, isLoading } = useBottlenecks(30);
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Bottlenecks</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {isLoading ? (
          <Skeleton className="h-52 w-full" />
        ) : (
          <>
            <div className="flex items-center gap-2 text-sm">
              <HelpTip label="The status stage that has accumulated the most total time across all tasks in the window">
                <span className="text-muted-foreground">Worst stage:</span>
              </HelpTip>
              {data?.worst_stage ? (
                <Badge variant="destructive">{label(data.worst_stage)}</Badge>
              ) : (
                <span className="text-muted-foreground">—</span>
              )}
              <HelpTip label="Tasks currently in the blocked status right now, across all teams">
                <span className="ml-auto text-muted-foreground">
                  {data?.active_blockers ?? 0} active blockers
                </span>
              </HelpTip>
            </div>
            <div className="space-y-2">
              {(data?.by_stage ?? []).slice(0, 6).map((s) => (
                <div key={s.status} className="space-y-1">
                  <div className="flex justify-between text-xs">
                    <span>{label(s.status)}</span>
                    <HelpTip label="Cumulative time spent in this stage across all tasks · how many tasks are sitting in it right now">
                      <span className="text-muted-foreground">
                        {fmtDuration(s.cumulative_seconds)} · {s.parked_now}{" "}
                        parked
                      </span>
                    </HelpTip>
                  </div>
                  <div className="h-2 w-full rounded bg-muted">
                    <div
                      className="h-2 rounded bg-[var(--chart-2)]"
                      style={{ width: `${Math.round(s.pct_of_total * 100)}%` }}
                    />
                  </div>
                </div>
              ))}
              {(data?.by_stage ?? []).length === 0 && (
                <p className="text-sm text-muted-foreground py-12 text-center">
                  No stage data yet.
                </p>
              )}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ─── Rework ───────────────────────────────────────────────────────────────────

function ReworkCard() {
  const { data, isLoading } = useRework(30);
  return (
    <Card>
      <CardHeader className="pb-2">
        <HelpTip label="A completed task 'bounces' when it's sent to needs_revision at least once — by QA, the PR gate, the PM, or the CEO">
          <CardTitle className="text-base">Rework (30d)</CardTitle>
        </HelpTip>
      </CardHeader>
      <CardContent className="space-y-4">
        {isLoading ? (
          <Skeleton className="h-52 w-full" />
        ) : (
          <>
            <div className="flex items-baseline gap-3">
              <span className="text-3xl font-bold">{pct(data?.rate ?? 0)}</span>
              <span className="text-sm text-muted-foreground">
                {data?.total_reworked ?? 0}/{data?.total_completed ?? 0} bounced
                · ${(data?.rework_cost_usd ?? 0).toFixed(2)} cost
              </span>
            </div>
            <div className="flex gap-2 text-xs">
              {(data?.by_team ?? []).map((t) => (
                <Badge key={t.team} variant="secondary">
                  {label(t.team)} {pct(t.rate)}
                </Badge>
              ))}
            </div>
            {(data?.by_agent ?? []).length > 0 && (
              <ResponsiveTable
                table={
                  <table className="w-full text-xs">
                    <thead className="text-muted-foreground">
                      <tr className="text-left">
                        <th className="py-1 font-medium">Agent</th>
                        <th className="py-1 font-medium text-right">
                          <HelpTip label="Share of this agent's completed tasks that bounced back for revision at least once">
                            <span>Rate</span>
                          </HelpTip>
                        </th>
                        <th className="py-1 font-medium text-right">
                          QA fails
                        </th>
                        <th className="py-1 font-medium text-right">
                          PR fails
                        </th>
                        <th className="py-1 font-medium text-right">
                          PM rejects
                        </th>
                        <th className="py-1 font-medium text-right">
                          CEO rejects
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {(data?.by_agent ?? []).slice(0, 8).map((a) => (
                        <tr
                          key={a.agent_slug}
                          className="border-t border-border/50"
                        >
                          <td className="py-1">{a.agent_slug}</td>
                          <td className="py-1 text-right">{pct(a.rate)}</td>
                          <td className="py-1 text-right">{a.qa_fails}</td>
                          <td className="py-1 text-right">{a.pr_fails}</td>
                          <td className="py-1 text-right">{a.pm_rejects}</td>
                          <td className="py-1 text-right">{a.ceo_rejects}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                }
                cards={
                  <ResponsiveTableCardList>
                    {(data?.by_agent ?? []).slice(0, 8).map((a) => (
                      <ResponsiveTableCard key={a.agent_slug}>
                        <span className="text-sm font-medium">
                          {a.agent_slug}
                        </span>
                        <div className="mt-2 divide-y">
                          <ResponsiveTableCardRow label="Rate">
                            {pct(a.rate)}
                          </ResponsiveTableCardRow>
                          <ResponsiveTableCardRow label="QA fails">
                            {a.qa_fails}
                          </ResponsiveTableCardRow>
                          <ResponsiveTableCardRow label="PR fails">
                            {a.pr_fails}
                          </ResponsiveTableCardRow>
                          <ResponsiveTableCardRow label="PM rejects">
                            {a.pm_rejects}
                          </ResponsiveTableCardRow>
                          <ResponsiveTableCardRow label="CEO rejects">
                            {a.ceo_rejects}
                          </ResponsiveTableCardRow>
                        </div>
                      </ResponsiveTableCard>
                    ))}
                  </ResponsiveTableCardList>
                }
              />
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ─── Per-cell scorecards ────────────────────────────────────────────────────────

function CellScorecard({ team }: { team: string }) {
  const { data, isLoading } = useTeamScorecard(team, 7);
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">{label(team)} (7d)</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : (
          <ScorecardBody card={data} />
        )}
      </CardContent>
    </Card>
  );
}

function ScorecardBody({ card }: { card: Scorecard | undefined }) {
  const stat = (k: string, v: string, tip?: string) => (
    <HelpTip label={tip}>
      <div className="flex justify-between text-sm">
        <span className="text-muted-foreground">{k}</span>
        <span className="font-medium">{v}</span>
      </div>
    </HelpTip>
  );
  return (
    <div className="space-y-1">
      {stat("Completed", String(card?.tasks_completed ?? 0))}
      {stat(
        "Avg cycle",
        card?.avg_cycle_hours != null
          ? card.avg_cycle_hours.toFixed(1) + "h"
          : "—",
        "Average wall-clock time from claim to completion",
      )}
      {stat(
        "Rework",
        pct(card?.rework_rate ?? 0),
        "Share of completed tasks that bounced back for revision at least once",
      )}
      {stat("Cost", "$" + (card?.cost_usd ?? 0).toFixed(2))}
    </div>
  );
}

// ─── Tab ──────────────────────────────────────────────────────────────────────

export function DeliveryTabContent() {
  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-2">
        <CycleTimeCard />
        <BottlenecksCard />
      </div>
      <ReworkCard />
      <div className="grid gap-4 md:grid-cols-3">
        {CELLS.map((team) => (
          <CellScorecard key={team} team={team} />
        ))}
      </div>
    </div>
  );
}
