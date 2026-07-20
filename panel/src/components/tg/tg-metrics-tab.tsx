"use client";

/**
 * Metrics tab — the spend & delivery drilldown. A period segmented control
 * drives a wallet-style hero (total spend + area chart) plus by-agent /
 * by-team / by-model breakdowns and delivery/efficiency health; tapping an
 * agent row pushes a per-agent drilldown sub-page.
 *
 * Every query branches on isTgDemoMode() inside its own queryFn (the same
 * shape use-approval-queue.ts and tg-today-tab.tsx already use) so demo
 * mode never touches the network — no live backend needed to style this.
 */

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { format } from "date-fns";
import { AlertTriangle } from "lucide-react";
import { usageApi, type UsagePeriod } from "@/lib/api/usage";
import { observabilityApi } from "@/lib/api/observability";
import { isScorecardMemberId } from "@/hooks/use-observability";
import { useAgents } from "@/hooks/use-agents";
import { getAgentDisplayName } from "@/lib/agent-utils";
import { isTgDemoMode } from "@/lib/telegram/demo";
import { fmtUsd, fmtTokens } from "@/components/tg/tg-format";
import { TgAreaChart } from "@/components/tg/charts";
import {
  TgAvatar,
  TgRow,
  TgSection,
  TgSegmented,
  TgStat,
  TgDeltaChip,
  TgSubPage,
  TG_CARD,
} from "@/components/tg/ui";
import { haptics } from "@/lib/telegram/webapp";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type {
  AgentUsageRow,
  CacheEfficiencyResponse,
  MemberScorecard,
  ModelUsageSlice,
  ReworkReport,
  StageTiming,
  SpawnWasteResponse,
  TeamUsageRow,
  UsageProjection,
  UsageSummary,
  UsageTimePoint,
} from "@/types";
import {
  DEMO_AGENT_USAGE,
  DEMO_DELIVERY,
  DEMO_EFFICIENCY,
  DEMO_MEMBER_SCORECARD,
  DEMO_MODEL_USAGE,
  DEMO_TEAM_USAGE,
  DEMO_USAGE_SERIES,
  DEMO_USAGE_SUMMARY,
} from "@/components/tg/tg-metrics-demo";

type Period = UsagePeriod;
type ViewState =
  { kind: "hub" } | { kind: "agent"; slug: string; row: AgentUsageRow };

const PERIOD_OPTIONS: ReadonlyArray<{ value: Period; label: string }> = [
  { value: "24h", label: "1D" },
  { value: "7d", label: "1W" },
  { value: "30d", label: "1M" },
  { value: "90d", label: "3M" },
];

const PERIOD_DAYS: Record<Period, number> = {
  "24h": 1,
  "7d": 7,
  "30d": 30,
  "90d": 90,
};

const REFRESH_MS = 60_000;

// =============================================================================
// QUERIES — one per data need, each demo-gated inside its own queryFn.
// =============================================================================

function useSummary(period: Period) {
  return useQuery<UsageSummary>({
    queryKey: ["tg-metrics", "summary", period],
    queryFn: () =>
      isTgDemoMode() ? DEMO_USAGE_SUMMARY : usageApi.getUsageSummary(period),
    refetchInterval: REFRESH_MS,
  });
}

function useSeries(period: Period, slug?: string) {
  return useQuery<UsageTimePoint[]>({
    queryKey: ["tg-metrics", "series", period, slug ?? null],
    queryFn: () => {
      if (!isTgDemoMode()) return usageApi.getUsageTimeSeries(period, slug);
      if (!slug) return DEMO_USAGE_SERIES;
      const row = DEMO_AGENT_USAGE.find((a) => a.agent_slug === slug);
      const scale = (row?.pct_of_total ?? 0) / 100;
      return DEMO_USAGE_SERIES.map((p) => ({
        ...p,
        cost_usd: parseFloat((p.cost_usd * scale).toFixed(2)),
      }));
    },
    refetchInterval: REFRESH_MS * 2,
  });
}

function useAgentRows(period: Period) {
  return useQuery<AgentUsageRow[]>({
    queryKey: ["tg-metrics", "by-agent", period],
    queryFn: () =>
      isTgDemoMode() ? DEMO_AGENT_USAGE : usageApi.getAgentUsage(period),
    refetchInterval: REFRESH_MS,
  });
}

function useTeamRows(period: Period) {
  return useQuery<TeamUsageRow[]>({
    queryKey: ["tg-metrics", "by-team", period],
    queryFn: () =>
      isTgDemoMode() ? DEMO_TEAM_USAGE : usageApi.getTeamUsage(period),
    refetchInterval: REFRESH_MS,
  });
}

function useModelRows(period: Period) {
  return useQuery<ModelUsageSlice[]>({
    queryKey: ["tg-metrics", "by-model", period],
    queryFn: () =>
      isTgDemoMode() ? DEMO_MODEL_USAGE : usageApi.getModelUsage(period),
    refetchInterval: REFRESH_MS,
  });
}

function useDelivery(days: number) {
  return useQuery<{ rework: ReworkReport; cycle: StageTiming[] }>({
    queryKey: ["tg-metrics", "delivery", days],
    queryFn: async () => {
      if (isTgDemoMode()) return DEMO_DELIVERY;
      const [rework, cycle] = await Promise.all([
        observabilityApi.getRework(days),
        observabilityApi.getCycleTime(days),
      ]);
      return { rework, cycle };
    },
    refetchInterval: REFRESH_MS,
  });
}

function useEfficiency(period: Period) {
  return useQuery<{
    cache: CacheEfficiencyResponse;
    projection: UsageProjection;
    spawnWaste: SpawnWasteResponse;
  }>({
    queryKey: ["tg-metrics", "efficiency", period],
    queryFn: async () => {
      if (isTgDemoMode()) return DEMO_EFFICIENCY;
      const [cache, projection, spawnWaste] = await Promise.all([
        usageApi.getCacheEfficiency(period),
        usageApi.getUsageProjection(),
        usageApi.getSpawnWaste(period),
      ]);
      return { cache, projection, spawnWaste };
    },
    refetchInterval: REFRESH_MS * 2,
  });
}

/** Member scorecard needs a real agent UUID (the endpoint's path param is
 * UUID-typed) — `agentId` is undefined until the roster resolves the tapped
 * slug. Skips (returns null, never throws) on an unresolved/placeholder id
 * so the Scorecard section just hides instead of erroring. */
function useAgentScorecard(agentId: string | undefined, days: number) {
  return useQuery<MemberScorecard | null>({
    queryKey: ["tg-metrics", "scorecard", agentId ?? null, days],
    queryFn: () => {
      if (isTgDemoMode()) return DEMO_MEMBER_SCORECARD;
      if (!agentId || !isScorecardMemberId(agentId)) return null;
      return observabilityApi.getMemberScorecard(agentId, days);
    },
    enabled: isTgDemoMode() || Boolean(agentId),
  });
}

// =============================================================================
// FORMATTING HELPERS
// =============================================================================

function bucketLabel(period: Period, iso: string): string {
  return format(new Date(iso), period === "24h" ? "HH:mm" : "MMM d");
}

function humanizeHours(seconds: number): string {
  return `${(seconds / 3600).toFixed(1)}h`;
}

function humanizeStatus(status: string): string {
  const spaced = status.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function teamLabel(team: string): string {
  if (team === "ux_ui") return "UX/UI";
  if (team === "main_pm") return "Main PM";
  return team.charAt(0).toUpperCase() + team.slice(1);
}

function pctOrDash(v: number | null): string {
  return v === null ? "—" : `${(v * 100).toFixed(0)}%`;
}

// =============================================================================
// SHARED SUBCOMPONENTS
// =============================================================================

function ErrorCard({ onRetry }: { onRetry: () => void }) {
  return (
    <div
      className={cn(
        TG_CARD,
        "flex flex-col items-center gap-3 p-6 text-center",
      )}
    >
      <AlertTriangle className="h-6 w-6 text-muted-foreground" />
      <p className="text-sm text-muted-foreground">
        Couldn&apos;t load metrics. Pull to retry.
      </p>
      <Button size="sm" onClick={onRetry}>
        Retry
      </Button>
    </div>
  );
}

function SkeletonBlocks() {
  return (
    <div className="space-y-3">
      <div className="h-40 animate-pulse rounded-[20px] bg-card" />
      <div className="h-28 animate-pulse rounded-[20px] bg-card" />
      <div className="h-28 animate-pulse rounded-[20px] bg-card" />
    </div>
  );
}

function ThinBarRow({
  label,
  pct,
  trailing,
}: {
  label: React.ReactNode;
  pct: number;
  trailing: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-3 py-1.5">
      <div className="min-w-0 flex-1">
        <p className="truncate text-[13px] font-medium leading-snug">{label}</p>
        <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-muted">
          <div
            className="h-full rounded-full bg-primary"
            style={{ width: `${Math.min(100, Math.max(0, pct))}%` }}
          />
        </div>
      </div>
      <span className="tg-display shrink-0 text-sm">{trailing}</span>
    </div>
  );
}

function SpendHero({
  cost,
  tokensCaption,
  pct,
  values,
  startLabel,
  endLabel,
  deltaPct,
}: {
  cost: number;
  tokensCaption?: string;
  pct?: string;
  values: number[];
  startLabel?: string;
  endLabel?: string;
  deltaPct?: number | null;
}) {
  return (
    <div className={cn(TG_CARD, "p-4")}>
      <p className="text-[13px] text-muted-foreground">Spend</p>
      <div className="mt-1 flex items-end justify-between gap-3">
        <span className="tg-display text-[40px] leading-none">
          {fmtUsd(cost)}
        </span>
        {deltaPct !== undefined && <TgDeltaChip pct={deltaPct} />}
      </div>
      <p className="mt-0.5 text-xs text-muted-foreground">
        {pct ?? tokensCaption}
      </p>
      <div className="mt-3">
        <TgAreaChart
          values={values}
          format={(v) => fmtUsd(v)}
          startLabel={startLabel}
          endLabel={endLabel}
        />
      </div>
      {cost === 0 && (
        <p className="mt-1 text-xs text-muted-foreground">
          No spend yet this period
        </p>
      )}
    </div>
  );
}

// =============================================================================
// HUB
// =============================================================================

function Hub({
  period,
  onSelectAgent,
}: {
  period: Period;
  onSelectAgent: (row: AgentUsageRow) => void;
}) {
  const summaryQ = useSummary(period);
  const seriesQ = useSeries(period);
  const agentsQ = useAgentRows(period);
  const teamsQ = useTeamRows(period);
  const modelsQ = useModelRows(period);
  const days = PERIOD_DAYS[period];
  const deliveryQ = useDelivery(days);
  const efficiencyQ = useEfficiency(period);

  if (summaryQ.isLoading || seriesQ.isLoading) return <SkeletonBlocks />;
  if (summaryQ.isError || !summaryQ.data) {
    return <ErrorCard onRetry={() => summaryQ.refetch()} />;
  }

  const summary = summaryQ.data;
  const series = seriesQ.data ?? [];
  const first = series[0];
  const last = series[series.length - 1];

  const agentRows = [...(agentsQ.data ?? [])].sort(
    (a, b) => b.cost_usd - a.cost_usd,
  );
  const topAgents = agentRows.slice(0, 8);
  const teamRows = teamsQ.data ?? [];
  const modelRows = modelsQ.data ?? [];

  const rework = deliveryQ.data?.rework;
  const cycle = deliveryQ.data?.cycle ?? [];
  const worstStage = [...cycle].sort(
    (a, b) => b.avg_seconds - a.avg_seconds,
  )[0];
  const bounced = (rework?.by_agent ?? [])
    .map((a) => ({
      ...a,
      bounces: a.qa_fails + a.pr_fails + a.pm_rejects + a.ceo_rejects,
    }))
    .sort((a, b) => b.bounces - a.bounces)
    .slice(0, 3);

  const efficiency = efficiencyQ.data;

  return (
    <div className="tg-stagger space-y-3">
      <SpendHero
        cost={summary.total_cost_usd}
        tokensCaption={`${fmtTokens(summary.total_tokens)} tokens`}
        values={series.map((p) => p.cost_usd)}
        startLabel={first ? bucketLabel(period, first.bucket) : undefined}
        endLabel={last ? bucketLabel(period, last.bucket) : undefined}
        deltaPct={summary.trend_pct}
      />

      <TgSection title="By agent">
        {topAgents.length === 0 ? (
          <p className="py-2 text-sm text-muted-foreground">
            No agent spend yet.
          </p>
        ) : (
          <>
            <div className="-mx-1.5 divide-y divide-border/50">
              {topAgents.map((row) => (
                <TgRow
                  key={row.agent_slug}
                  leading={<TgAvatar name={row.agent_slug} />}
                  title={getAgentDisplayName(row.agent_slug)}
                  meta={`${row.pct_of_total.toFixed(0)}% of spend`}
                  trailing={
                    <span className="tg-display text-sm">
                      {fmtUsd(row.cost_usd)}
                    </span>
                  }
                  onPress={() => {
                    haptics.tap();
                    onSelectAgent(row);
                  }}
                />
              ))}
            </div>
            {agentRows.length > 8 && (
              <p className="mt-1 px-1.5 text-xs text-muted-foreground">
                +{agentRows.length - 8} more
              </p>
            )}
          </>
        )}
      </TgSection>

      <TgSection title="By team">
        {teamRows.length === 0 ? (
          <p className="py-2 text-sm text-muted-foreground">
            No team spend yet.
          </p>
        ) : (
          teamRows.map((row) => (
            <ThinBarRow
              key={row.team}
              label={teamLabel(row.team)}
              pct={row.pct_of_total}
              trailing={fmtUsd(row.cost_usd)}
            />
          ))
        )}
      </TgSection>

      <TgSection title="By model">
        {modelRows.length === 0 ? (
          <p className="py-2 text-sm text-muted-foreground">
            No model spend yet.
          </p>
        ) : (
          modelRows.map((row) => (
            <ThinBarRow
              key={row.model}
              label={<span className="truncate">{row.model}</span>}
              pct={row.pct_of_total}
              trailing={fmtUsd(row.cost_usd)}
            />
          ))
        )}
      </TgSection>

      <TgSection title="Delivery">
        <div className="grid grid-cols-2 gap-3">
          <TgStat
            value={`${((rework?.rate ?? 0) * 100).toFixed(0)}%`}
            caption="Rework rate"
            tone={(rework?.rate ?? 0) > 0.2 ? "attention" : "default"}
          />
          <TgStat value={rework?.total_completed ?? 0} caption="Completed" />
          <TgStat
            value={worstStage ? humanizeHours(worstStage.avg_seconds) : "—"}
            caption={
              worstStage ? humanizeStatus(worstStage.status) : "Slowest stage"
            }
          />
          <TgStat
            value={fmtUsd(rework?.rework_cost_usd ?? 0)}
            caption="Rework cost"
          />
        </div>
        {bounced.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-1.5">
            {bounced.map((a) => (
              <span
                key={a.agent_slug}
                className="rounded-full bg-muted px-2 py-1 text-xs text-muted-foreground"
              >
                {getAgentDisplayName(a.agent_slug)} · {a.bounces} bounces
              </span>
            ))}
          </div>
        )}
      </TgSection>

      <TgSection title="Efficiency">
        <div className="grid grid-cols-2 gap-3">
          <TgStat
            value={`${((efficiency?.cache.cache_hit_rate ?? 0) * 100).toFixed(0)}%`}
            caption="Cache hit rate"
          />
          <TgStat
            value={fmtUsd(efficiency?.cache.cost_saved_by_cache_usd ?? 0)}
            caption="Saved by cache"
          />
          <TgStat
            value={fmtUsd(
              efficiency?.projection.projected_monthly_cost_usd ?? 0,
            )}
            caption="Projected monthly"
          />
          <TgStat
            value={`${(efficiency?.spawnWaste.unproductive_pct ?? 0).toFixed(0)}%`}
            caption="Spawn waste"
            tone={
              (efficiency?.spawnWaste.unproductive_pct ?? 0) > 25
                ? "attention"
                : "default"
            }
          />
        </div>
      </TgSection>
    </div>
  );
}

// =============================================================================
// AGENT DRILLDOWN
// =============================================================================

function AgentDrilldown({
  slug,
  row,
  period,
  agentId,
  onBack,
}: {
  slug: string;
  row: AgentUsageRow;
  period: Period;
  agentId: string | undefined;
  onBack: () => void;
}) {
  const seriesQ = useSeries(period, slug);
  const days = PERIOD_DAYS[period];
  const scorecardQ = useAgentScorecard(agentId, days);

  const name = getAgentDisplayName(slug);

  if (seriesQ.isLoading) {
    return (
      <TgSubPage title={name} subtitle={slug} onBack={onBack}>
        <SkeletonBlocks />
      </TgSubPage>
    );
  }
  if (seriesQ.isError) {
    return (
      <TgSubPage title={name} subtitle={slug} onBack={onBack}>
        <ErrorCard onRetry={() => seriesQ.refetch()} />
      </TgSubPage>
    );
  }

  const series = seriesQ.data ?? [];
  const first = series[0];
  const last = series[series.length - 1];
  const scorecard = scorecardQ.data;

  return (
    <TgSubPage title={name} subtitle={slug} onBack={onBack}>
      <div className="tg-stagger space-y-3">
        <SpendHero
          cost={row.cost_usd}
          pct={`${row.pct_of_total.toFixed(0)}% of org spend`}
          values={series.map((p) => p.cost_usd)}
          startLabel={first ? bucketLabel(period, first.bucket) : undefined}
          endLabel={last ? bucketLabel(period, last.bucket) : undefined}
        />

        {scorecard && (
          <TgSection title="Scorecard">
            <div className="grid grid-cols-2 gap-3">
              <TgStat
                value={scorecard.tasks_completed}
                caption="Tasks completed"
              />
              <TgStat
                value={pctOrDash(scorecard.first_pass_yield)}
                caption="First-pass yield"
              />
              <TgStat
                value={pctOrDash(scorecard.utilization)}
                caption="Utilization"
              />
              <TgStat value={fmtTokens(scorecard.tokens)} caption="Tokens" />
              <TgStat
                value={scorecard.revisions_received}
                caption="Revisions received"
              />
              <TgStat value={scorecard.escalations} caption="Escalations" />
            </div>
          </TgSection>
        )}
      </div>
    </TgSubPage>
  );
}

// =============================================================================
// ROOT
// =============================================================================

export function TgMetricsTab() {
  const [period, setPeriod] = useState<Period>("7d");
  const [view, setView] = useState<ViewState>({ kind: "hub" });
  const { data: agents } = useAgents();

  const agentId = useMemo(() => {
    if (view.kind !== "agent") return undefined;
    return agents?.find((a) => a.agent_id === view.slug)?.id;
  }, [agents, view]);

  if (view.kind === "agent") {
    return (
      <AgentDrilldown
        slug={view.slug}
        row={view.row}
        period={period}
        agentId={agentId}
        onBack={() => setView({ kind: "hub" })}
      />
    );
  }

  return (
    <div className="space-y-3">
      <TgSegmented
        options={PERIOD_OPTIONS}
        value={period}
        onChange={setPeriod}
      />
      <Hub
        period={period}
        onSelectAgent={(row) =>
          setView({ kind: "agent", slug: row.agent_slug, row })
        }
      />
    </div>
  );
}
