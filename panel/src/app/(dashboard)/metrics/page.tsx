"use client";

import { Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { useOrchestratorStatus } from "@/hooks/use-agents";
import { useTasks } from "@/hooks/use-tasks";
import {
  useUsageSummary,
  useUsageTimeSeries,
  useAgentUsage,
  useTeamUsage,
  useModelUsage,
  useRoleUsage,
  useUsageProjection,
  useCacheEfficiency,
  useSpawnWaste,
  useUsageSessions,
} from "@/hooks/use-usage";
import { TaskStatus, Team } from "@/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { OfflineState } from "@/components/ui/offline-state";
import { DeliveryTabContent } from "@/components/metrics/delivery-tab";
import { ScorecardsTabContent } from "@/components/metrics/scorecards-tab";
import {
  UsageTimeSeriesChart,
  ModelUsageDonut,
  AgentUsageChart,
  TeamUsageChart,
  SessionsTable,
} from "@/components/metrics";
import {
  Activity,
  TrendingUp,
  TrendingDown,
  Clock,
  AlertTriangle,
  Users,
  CheckCircle,
  XCircle,
  Zap,
  Timer,
  Coins,
  Sparkles,
} from "lucide-react";
import type {
  UsageProjection as UP,
  CacheEfficiencyResponse as CER,
  RoleUsageRow,
  SpawnWasteResponse,
} from "@/types";

// ─── Humanized number formatting ─────────────────────────────────────────────

/** Format counts with K/M suffix for values >= 1000. */
function humanizeCount(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "K";
  return String(n);
}

/** Format token counts (same as humanizeCount but used for token display). */
function fmtTokens(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "K";
  return String(n);
}

// ─── Shared sub-components ────────────────────────────────────────────────────

interface MetricCardProps {
  title: string;
  value: string | number;
  subtitle?: string;
  icon: React.ReactNode;
  trend?: "up" | "down" | "neutral";
  trendValue?: string;
}

function MetricCard({
  title,
  value,
  subtitle,
  icon,
  trend,
  trendValue,
}: MetricCardProps) {
  const displayValue = typeof value === "number" ? humanizeCount(value) : value;
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">
          {title}
        </CardTitle>
        {icon}
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold">{displayValue}</div>
        {subtitle && (
          <p className="text-xs text-muted-foreground mt-1">{subtitle}</p>
        )}
        {trend && trendValue && (
          <div
            className={
              "flex items-center gap-1 mt-2 text-xs " +
              (trend === "up"
                ? "text-green-600"
                : trend === "down"
                  ? "text-red-600"
                  : "text-gray-500")
            }
          >
            <TrendingUp
              className={"h-3 w-3 " + (trend === "down" ? "rotate-180" : "")}
            />
            {trendValue}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

interface TeamHealthCardProps {
  team: Team;
  activeTasks: number;
  blockedTasks: number;
  completedToday: number;
}

function TeamHealthCard({
  team,
  activeTasks,
  blockedTasks,
  completedToday,
}: TeamHealthCardProps) {
  const healthScore = Math.max(0, 100 - blockedTasks * 20);

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium capitalize">
          {team.replace(/_/g, " ")} Cell
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex items-center gap-2 mb-3">
          <Progress value={healthScore} className="flex-1" />
          <span className="text-sm font-medium">{healthScore}%</span>
        </div>
        <div className="grid grid-cols-3 gap-2 text-center text-xs">
          <div>
            <div className="font-semibold text-blue-600">{activeTasks}</div>
            <div className="text-muted-foreground">Active</div>
          </div>
          <div>
            <div className="font-semibold text-red-600">{blockedTasks}</div>
            <div className="text-muted-foreground">Blocked</div>
          </div>
          <div>
            <div className="font-semibold text-green-600">{completedToday}</div>
            <div className="text-muted-foreground">Done</div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ─── Performance tab content ─────────────────────────────────────────────────

function PerformanceTabContent() {
  const { data: tasks, error: tasksError, refetch: refetchTasks } = useTasks();
  const {
    data: status,
    error: statusError,
    refetch: refetchStatus,
  } = useOrchestratorStatus();

  const isOffline =
    (tasksError || statusError) &&
    (tasksError?.message?.includes("Network Error") ||
      statusError?.message?.includes("Network Error"));

  const refetch = () => {
    refetchTasks();
    refetchStatus();
  };

  const taskList = tasks || [];
  const agentList = status?.agents || [];

  // Velocity metrics
  const completedToday = taskList.filter((t) => {
    if (!t.completed_at) return false;
    const completed = new Date(t.completed_at);
    const today = new Date();
    return completed.toDateString() === today.toDateString();
  }).length;

  const completedThisWeek = taskList.filter((t) => {
    if (!t.completed_at) return false;
    const completed = new Date(t.completed_at);
    const weekAgo = new Date();
    weekAgo.setDate(weekAgo.getDate() - 7);
    return completed > weekAgo;
  }).length;

  // Task status counts
  const pending = taskList.filter(
    (t) => t.status === TaskStatus.PENDING,
  ).length;
  const inProgress = taskList.filter(
    (t) => t.status === TaskStatus.IN_PROGRESS,
  ).length;
  const blocked = taskList.filter(
    (t) => t.status === TaskStatus.BLOCKED,
  ).length;
  const awaitingQa = taskList.filter(
    (t) => t.status === TaskStatus.AWAITING_QA,
  ).length;
  const completed = taskList.filter(
    (t) => t.status === TaskStatus.COMPLETED,
  ).length;

  // Agent counts
  const runningAgents =
    status?.by_state?.running ||
    agentList.filter((a) => a.state === "running").length;
  const idleAgents =
    status?.by_state?.idle ||
    agentList.filter((a) => a.state === "idle" || a.state === "stopped").length;
  const waitingAgents =
    status?.waiting_count ||
    agentList.filter((a) => a.state === "waiting_long").length;
  const errorAgents =
    status?.by_state?.error ||
    agentList.filter((a) => a.state === "error").length;

  // Team metrics
  const teamMetrics = Object.values(Team).map((team) => {
    const teamTasks = taskList.filter((t) => t.team === team);
    return {
      team,
      activeTasks: teamTasks.filter((t) =>
        [TaskStatus.IN_PROGRESS, TaskStatus.CLAIMED].includes(t.status),
      ).length,
      blockedTasks: teamTasks.filter((t) => t.status === TaskStatus.BLOCKED)
        .length,
      completedToday: teamTasks.filter((t) => {
        if (!t.completed_at) return false;
        const c = new Date(t.completed_at);
        const today = new Date();
        return c.toDateString() === today.toDateString();
      }).length,
    };
  });

  if (isOffline) {
    return (
      <OfflineState
        title="Cannot Load Performance Metrics"
        description="Start the RoboCo orchestrator to view performance analytics."
        onRetry={refetch}
      />
    );
  }

  return (
    <div className="space-y-6">
      {/* Velocity Metrics */}
      <div>
        <h2 className="text-lg font-semibold mb-3">Velocity</h2>
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4 xl:grid-cols-4 2xl:grid-cols-4">
          <MetricCard
            title="Completed Today"
            value={completedToday}
            subtitle="Tasks finished"
            icon={<Zap className="h-4 w-4 text-green-500" />}
          />
          <MetricCard
            title="Completed This Week"
            value={completedThisWeek}
            subtitle="Rolling 7 days"
            icon={<TrendingUp className="h-4 w-4 text-blue-500" />}
          />
          <MetricCard
            title="Total Completed"
            value={completed}
            subtitle="All time"
            icon={<CheckCircle className="h-4 w-4 text-green-500" />}
          />
          <MetricCard
            title="Completion Rate"
            value={
              taskList.length > 0
                ? Math.round((completed / taskList.length) * 100) + "%"
                : "0%"
            }
            subtitle="Of all tasks"
            icon={<Activity className="h-4 w-4 text-purple-500" />}
          />
        </div>
      </div>

      {/* Task Status */}
      <div>
        <h2 className="text-lg font-semibold mb-3">Task Status</h2>
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-5 xl:grid-cols-5 2xl:grid-cols-5">
          <MetricCard
            title="Pending"
            value={pending}
            icon={<Clock className="h-4 w-4 text-gray-500" />}
          />
          <MetricCard
            title="In Progress"
            value={inProgress}
            icon={<Activity className="h-4 w-4 text-blue-500" />}
          />
          <MetricCard
            title="Blocked"
            value={blocked}
            icon={<AlertTriangle className="h-4 w-4 text-red-500" />}
          />
          <MetricCard
            title="Awaiting QA"
            value={awaitingQa}
            icon={<Timer className="h-4 w-4 text-yellow-500" />}
          />
          <MetricCard
            title="Completed"
            value={completed}
            icon={<CheckCircle className="h-4 w-4 text-green-500" />}
          />
        </div>
      </div>

      {/* Agent Status */}
      <div>
        <h2 className="text-lg font-semibold mb-3">Agent Status</h2>
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4 xl:grid-cols-4 2xl:grid-cols-4">
          <MetricCard
            title="Running"
            value={runningAgents}
            subtitle="Active agents"
            icon={<Users className="h-4 w-4 text-green-500" />}
          />
          <MetricCard
            title="Idle"
            value={idleAgents}
            subtitle="Available"
            icon={<Users className="h-4 w-4 text-gray-500" />}
          />
          <MetricCard
            title="Waiting"
            value={waitingAgents}
            subtitle="Needs input"
            icon={<Clock className="h-4 w-4 text-yellow-500" />}
          />
          <MetricCard
            title="Errors"
            value={errorAgents}
            subtitle="Failed agents"
            icon={<XCircle className="h-4 w-4 text-red-500" />}
          />
        </div>
      </div>

      {/* Team Health */}
      <div>
        <h2 className="text-lg font-semibold mb-3">Team Health</h2>
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-5 xl:grid-cols-5 2xl:grid-cols-6">
          {teamMetrics.map((tm) => (
            <TeamHealthCard
              key={tm.team}
              team={tm.team}
              activeTasks={tm.activeTasks}
              blockedTasks={tm.blockedTasks}
              completedToday={tm.completedToday}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

// ─── Token Usage & Costs tab content ─────────────────────────────────────────

function TokenUsageCostsSection() {
  const { data: summary, isLoading: loadingSnap } = useUsageSummary("24h");
  const { data: timeSeries, isLoading: loadingTS } = useUsageTimeSeries("24h");
  const { data: agentUsage, isLoading: loadingAgents } = useAgentUsage("24h");
  const { data: teamUsage, isLoading: loadingTeams } = useTeamUsage("24h");
  const { data: sessions, isLoading: loadingSessions } = useUsageSessions(100);
  const { data: modelUsage, isLoading: loadingModels } = useModelUsage("24h");
  const { data: projection, isLoading: loadingProj } = useUsageProjection();
  const { data: cacheStats, isLoading: loadingCache } =
    useCacheEfficiency("24h");
  const { data: roleUsage, isLoading: loadingRoles } = useRoleUsage("24h");
  const { data: waste, isLoading: loadingWaste } = useSpawnWaste("24h");

  const trendUp = (summary?.trend_pct ?? 0) >= 0;

  return (
    <div className="space-y-6">
      {/* Row 1 — Summary cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 2xl:grid-cols-6">
        <SummaryCard
          title="Tokens Input"
          value={summary ? fmtTokens(summary.tokens_input) : undefined}
          icon={<Zap className="h-4 w-4 text-yellow-500" />}
          isLoading={loadingSnap}
        />
        <SummaryCard
          title="Tokens Output"
          value={summary ? fmtTokens(summary.tokens_output) : undefined}
          icon={<Zap className="h-4 w-4 text-blue-500" />}
          isLoading={loadingSnap}
        />
        <SummaryCard
          title="Total Cost (24h)"
          value={summary ? "$" + summary.total_cost_usd.toFixed(4) : undefined}
          icon={<Coins className="h-4 w-4 text-green-500" />}
          isLoading={loadingSnap}
        />
        <SummaryCard
          title="Trend vs Prior"
          value={
            summary
              ? (trendUp ? "+" : "") + summary.trend_pct.toFixed(1) + "%"
              : undefined
          }
          icon={
            trendUp ? (
              <TrendingUp className="h-4 w-4 text-red-500" />
            ) : (
              <TrendingDown className="h-4 w-4 text-green-500" />
            )
          }
          isLoading={loadingSnap}
        />
        <SummaryCard
          title="Total Tokens"
          value={summary ? fmtTokens(summary.total_tokens) : undefined}
          icon={<Activity className="h-4 w-4 text-blue-500" />}
          isLoading={loadingSnap}
        />
        <SummaryCard
          title="Cache Saved"
          value={
            cacheStats
              ? "$" + cacheStats.cost_saved_by_cache_usd.toFixed(4)
              : undefined
          }
          icon={<Sparkles className="h-4 w-4 text-purple-500" />}
          isLoading={loadingCache}
        />
      </div>

      {/* Row 2 — Time series + model donut */}
      <div className="grid gap-4 lg:grid-cols-3 xl:grid-cols-3 2xl:grid-cols-3">
        <div className="lg:col-span-2">
          <UsageTimeSeriesChart data={timeSeries} isLoading={loadingTS} />
        </div>
        <ModelUsageDonut data={modelUsage} isLoading={loadingModels} />
      </div>

      {/* Row 3 — Agent bar + team bar */}
      <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-2 2xl:grid-cols-2">
        <AgentUsageChart data={agentUsage} isLoading={loadingAgents} />
        <TeamUsageChart data={teamUsage} isLoading={loadingTeams} />
      </div>

      {/* Row 4 — Projection + cache efficiency */}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-2 2xl:grid-cols-2">
        <ProjectionCard projection={projection} isLoading={loadingProj} />
        <CacheEfficiencyCard cacheStats={cacheStats} isLoading={loadingCache} />
      </div>

      {/* Row 5 — Per-role cost/cache + spawn waste */}
      <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-2 2xl:grid-cols-2">
        <RoleUsageTable data={roleUsage} isLoading={loadingRoles} />
        <SpawnWasteCard data={waste} isLoading={loadingWaste} />
      </div>

      {/* Row 6 — Sessions table */}
      <SessionsTable data={sessions} isLoading={loadingSessions} />
    </div>
  );
}

// ─── Helper sub-components ────────────────────────────────────────────────────

interface SummaryCardProps {
  title: string;
  value: string | undefined;
  icon: React.ReactNode;
  trend?: { dir: "up" | "down"; label: string };
  isLoading: boolean;
}

function SummaryCard({
  title,
  value,
  icon,
  trend,
  isLoading,
}: SummaryCardProps) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">
          {title}
        </CardTitle>
        {icon}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-7 w-24" />
        ) : (
          <>
            <div className="text-2xl font-bold">{value ?? "—"}</div>
            {trend && (
              <p
                className={
                  "text-xs mt-1 " +
                  (trend.dir === "up" ? "text-red-500" : "text-green-500")
                }
              >
                {trend.label}
              </p>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

interface ProjectionCardProps {
  projection: UP | undefined;
  isLoading: boolean;
}

function ProjectionCard({ projection, isLoading }: ProjectionCardProps) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base flex items-center gap-2">
          <TrendingUp className="h-4 w-4 text-blue-500" />
          Monthly Projection
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-10 w-full" />
        ) : (
          <div>
            <div className="text-3xl font-bold">
              {projection != null
                ? "$" + projection.projected_monthly_cost_usd.toFixed(2)
                : "—"}
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              Based on {projection?.basis_days ?? 7}-day rolling average ($
              {projection?.avg_daily_cost_usd.toFixed(4) ?? "—"}/day)
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

interface CacheEfficiencyCardProps {
  cacheStats: CER | undefined;
  isLoading: boolean;
}

function CacheEfficiencyCard({
  cacheStats,
  isLoading,
}: CacheEfficiencyCardProps) {
  const pct = cacheStats ? cacheStats.cache_hit_rate * 100 : 0;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-purple-500" />
          Cache Efficiency
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-10 w-full" />
        ) : (
          <div>
            <div className="text-3xl font-bold">{pct.toFixed(1)}%</div>
            <p className="text-xs text-muted-foreground mt-1">
              {cacheStats ? fmtTokens(cacheStats.tokens_cache_read) : "—"} cache
              reads · saved $
              {cacheStats?.cost_saved_by_cache_usd.toFixed(4) ?? "—"}
            </p>
            <Progress value={pct} className="mt-2" />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

interface RoleUsageTableProps {
  data: RoleUsageRow[] | undefined;
  isLoading: boolean;
}

function RoleUsageTable({ data, isLoading }: RoleUsageTableProps) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base flex items-center gap-2">
          <Users className="h-4 w-4 text-blue-500" />
          Cost &amp; Cache by Role
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : !data || data.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No usage recorded yet.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-muted-foreground">
                <th className="pb-1 font-medium">Role</th>
                <th className="pb-1 font-medium text-right">Cost</th>
                <th className="pb-1 font-medium text-right">Cache hit</th>
                <th className="pb-1 font-medium text-right">%</th>
              </tr>
            </thead>
            <tbody>
              {data.map((r) => (
                <tr key={r.role} className="border-t">
                  <td className="py-1 font-mono text-xs">{r.role}</td>
                  <td className="py-1 text-right">${r.cost_usd.toFixed(4)}</td>
                  <td className="py-1 text-right">
                    {(r.cache_hit_rate * 100).toFixed(1)}%
                  </td>
                  <td className="py-1 text-right text-muted-foreground">
                    {r.pct_of_total.toFixed(1)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  );
}

interface SpawnWasteCardProps {
  data: SpawnWasteResponse | undefined;
  isLoading: boolean;
}

function SpawnWasteCard({ data, isLoading }: SpawnWasteCardProps) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base flex items-center gap-2">
          <AlertTriangle className="h-4 w-4 text-orange-500" />
          Spawn Waste
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : !data ? (
          <p className="text-sm text-muted-foreground">No spawn data yet.</p>
        ) : (
          <div className="space-y-2">
            <div>
              <div className="text-3xl font-bold">
                {data.unproductive_pct.toFixed(1)}%
              </div>
              <p className="text-xs text-muted-foreground mt-1">
                {data.unproductive_spawns} of {data.total_spawns} spawns
                produced no output
              </p>
            </div>
            {data.by_role.length > 0 && (
              <table className="w-full text-xs">
                <tbody>
                  {data.by_role.map((r) => (
                    <tr key={r.role} className="border-t">
                      <td className="py-1 font-mono">{r.role}</td>
                      <td className="py-1 text-right text-muted-foreground">
                        {r.unproductive}/{r.spawns}
                      </td>
                      <td className="py-1 text-right">
                        {r.unproductive_pct.toFixed(0)}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {data.respawn_strikes.length > 0 && (
              <p className="text-xs text-muted-foreground">
                {data.respawn_strikes.length} wedged task
                {data.respawn_strikes.length === 1 ? "" : "s"} with open respawn
                strikes
              </p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ─── Tab types ────────────────────────────────────────────────────────────────

type MetricsTab = "performance" | "token-usage" | "delivery" | "scorecards";

const VALID_METRICS_TABS: MetricsTab[] = [
  "performance",
  "token-usage",
  "delivery",
  "scorecards",
];

function isValidMetricsTab(value: string | null): value is MetricsTab {
  return VALID_METRICS_TABS.includes(value as MetricsTab);
}

// ─── Main page content (uses useSearchParams) ─────────────────────────────────

function MetricsPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  // Read ?tab= from URL, default to "performance"
  const rawTab = searchParams.get("tab");
  const activeTab: MetricsTab = isValidMetricsTab(rawTab)
    ? rawTab
    : "performance";

  function handleTabChange(value: string) {
    const params = new URLSearchParams(searchParams.toString());
    params.set("tab", value);
    router.push(`?${params.toString()}`);
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Metrics</h1>
          <p className="text-muted-foreground">
            Performance analytics and operational insights
          </p>
        </div>
      </div>

      <Tabs value={activeTab} onValueChange={handleTabChange}>
        <TabsList>
          <TabsTrigger value="performance">Performance</TabsTrigger>
          <TabsTrigger value="token-usage">Token Usage</TabsTrigger>
          <TabsTrigger value="delivery">Delivery</TabsTrigger>
          <TabsTrigger value="scorecards">Scorecards</TabsTrigger>
        </TabsList>

        <TabsContent value="performance" className="mt-6">
          <PerformanceTabContent />
        </TabsContent>

        <TabsContent value="token-usage" className="mt-6">
          <TokenUsageCostsSection />
        </TabsContent>

        <TabsContent value="delivery" className="mt-6">
          <DeliveryTabContent />
        </TabsContent>

        <TabsContent value="scorecards" className="mt-6">
          <ScorecardsTabContent />
        </TabsContent>
      </Tabs>
    </div>
  );
}

// Wrap in Suspense for useSearchParams
export default function MetricsPage() {
  return (
    <Suspense
      fallback={
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <Skeleton className="h-9 w-32 mb-2" />
              <Skeleton className="h-5 w-72" />
            </div>
          </div>
          <div className="flex gap-2">
            <Skeleton className="h-9 w-28" />
            <Skeleton className="h-9 w-28" />
          </div>
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Card key={i}>
                <CardHeader className="pb-2">
                  <Skeleton className="h-4 w-24" />
                </CardHeader>
                <CardContent>
                  <Skeleton className="h-8 w-16" />
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      }
    >
      <MetricsPageContent />
    </Suspense>
  );
}
