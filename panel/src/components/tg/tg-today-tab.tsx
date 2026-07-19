"use client";

import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import api, { getErrorMessage } from "@/lib/api/client";
import { isTgDemoMode } from "@/lib/telegram/demo";
import { useWebSocket } from "@/hooks/use-websocket";
import {
  notificationKeys,
  useNotifications,
} from "@/hooks/use-notifications";
import { notificationsApi } from "@/lib/api/notifications";
import { projectsApi } from "@/lib/api/projects";
import { gitApi } from "@/lib/api/git";
import { haptics } from "@/lib/telegram/webapp";
import type { TgTab } from "@/components/tg/tg-tab-bar";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { TgAvatar, TgCircleAction, TgRow, TgSection } from "@/components/tg/ui";
import { TgSheet, useCountUp } from "@/components/tg/motion";
import {
  IconAckAll,
  IconFleet,
  IconShip,
  IconSweep,
} from "@/components/tg/tg-icons";
import { DayBars, Sparkline } from "@/components/tg/charts";
import {
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  CheckSquare,
  ChevronRight,
  Rocket,
} from "lucide-react";
import { toast } from "sonner";
import { formatDistanceToNow } from "date-fns";
import { cn } from "@/lib/utils";

export interface TodayTaskItem {
  id: string;
  title: string;
  status: string;
  team: string | null;
  updated_at: string | null;
}

export interface TodayBrief {
  needs_you: {
    total: number;
    awaiting_ceo_count: number;
    awaiting_ceo: TodayTaskItem[];
    blocked_count: number;
    blocked: TodayTaskItem[];
    held_drafts: Record<string, number>;
  };
  fleet: {
    total: number;
    by_status: Record<string, number>;
    working: Array<{
      name: string;
      role: string;
      team: string | null;
      task_title: string | null;
    }>;
  };
  spend: {
    tokens_today: number;
    cost_today_usd: number;
    series: number[];
    delta_pct: number | null;
  };
  velocity: { series: number[]; week_total: number };
  ship: {
    version: string;
    open_release_proposal: boolean;
    ci_fix_tasks: number;
  };
}

const REFETCH_MS = 45_000;

const DRAFT_LABELS: Record<string, string> = {
  release_proposals: "Release",
  x_posts: "X posts",
  video_posts: "Videos",
  roadmap_items: "Roadmap",
};

const DAY_LABELS = ["S", "M", "T", "W", "T", "F", "S"];

const compactNumber = new Intl.NumberFormat("en", {
  notation: "compact",
  maximumFractionDigits: 1,
});

function taskMeta(task: TodayTaskItem): string {
  const parts = [task.team ?? "—"];
  if (task.updated_at) {
    parts.push(`${formatDistanceToNow(new Date(task.updated_at))} ago`);
  }
  return parts.join(" · ");
}

/** Day-of-week initials for the trailing window ending today. */
function weekdayLabels(count: number): string[] {
  const today = new Date().getDay();
  return Array.from(
    { length: count },
    (_, i) => DAY_LABELS[(today - (count - 1 - i) + 7 * 2) % 7],
  );
}

function SpendHero({ spend }: { spend: TodayBrief["spend"] }) {
  const delta = spend.delta_pct;
  const up = (delta ?? 0) >= 0;
  const cost = useCountUp(spend.cost_today_usd);
  return (
    <div className="overflow-hidden rounded-2xl border bg-gradient-to-b from-primary/[0.07] to-transparent p-4">
      <p className="tg-display text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
        Spend today
      </p>
      <div className="mt-1 flex items-end justify-between gap-3">
        <div className="flex items-baseline gap-2">
          <span className="tg-display text-[40px] leading-none tabular-nums">
            ${cost.toFixed(2)}
          </span>
          {delta !== null && (
            <span
              className={cn(
                "flex items-center gap-0.5 text-xs font-medium tabular-nums",
                up ? "text-emerald-400" : "text-rose-400",
              )}
            >
              {up ? (
                <ArrowUpRight className="h-3.5 w-3.5" />
              ) : (
                <ArrowDownRight className="h-3.5 w-3.5" />
              )}
              {Math.abs(delta)}%
            </span>
          )}
        </div>
        <span className="pb-1 text-[11px] tabular-nums text-muted-foreground">
          {compactNumber.format(spend.tokens_today)} tokens
        </span>
      </div>
      <div className="-mx-1 mt-2">
        <Sparkline values={spend.series} />
      </div>
    </div>
  );
}

function NeedsYouBanner({
  needs,
  onApprovals,
  onBoard,
}: {
  needs: TodayBrief["needs_you"];
  onApprovals: () => void;
  onBoard: () => void;
}) {
  const heldEntries = Object.entries(needs.held_drafts).filter(
    ([, count]) => count > 0,
  );
  if (needs.total === 0) {
    return (
      <div className="flex items-center gap-2.5 rounded-2xl border bg-card p-3.5">
        <span className="flex h-9 w-9 items-center justify-center rounded-full bg-emerald-500/15 text-emerald-400">
          <CheckSquare className="h-4.5 w-4.5" />
        </span>
        <div>
          <p className="text-sm font-medium">All clear</p>
          <p className="text-[11px] text-muted-foreground">
            Nothing is waiting on you.
          </p>
        </div>
      </div>
    );
  }
  return (
    <div className="space-y-2 rounded-2xl border border-primary/30 bg-primary/[0.08] p-3.5">
      <button
        type="button"
        onClick={onApprovals}
        className="flex w-full items-center justify-between"
      >
        <span className="tg-display text-[11px] uppercase tracking-[0.14em] text-primary">
          Needs you
        </span>
        <span className="flex items-center gap-1 text-primary">
          <span className="rounded-full bg-primary px-2 py-0.5 text-[11px] font-semibold tabular-nums text-primary-foreground">
            {needs.total}
          </span>
          <ChevronRight className="h-4 w-4" />
        </span>
      </button>
      {heldEntries.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {heldEntries.map(([key, count]) => (
            <button
              key={key}
              type="button"
              onClick={onApprovals}
              className="rounded-full bg-primary/15 px-2.5 py-1 text-xs font-medium tabular-nums text-primary transition-colors active:bg-primary/25"
            >
              {DRAFT_LABELS[key] ?? key} · {count}
            </button>
          ))}
        </div>
      )}
      {(needs.awaiting_ceo.length > 0 || needs.blocked.length > 0) && (
        <div className="-mx-1.5 divide-y divide-primary/10">
          {needs.awaiting_ceo.slice(0, 2).map((t) => (
            <TgRow
              key={t.id}
              title={t.title}
              meta={taskMeta(t)}
              onPress={onBoard}
            />
          ))}
          {needs.blocked.slice(0, 2).map((t) => (
            <TgRow
              key={t.id}
              title={t.title}
              meta={
                <>
                  <span className="font-medium text-rose-400">blocked</span>
                  {" · "}
                  {taskMeta(t)}
                </>
              }
              onPress={onBoard}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/** Full working-roster sheet — every mid-task agent with its live task,
 * opened from the Fleet section's (truncated) preview. */
function FleetSheet({
  fleet,
  open,
  onClose,
}: {
  fleet: TodayBrief["fleet"];
  open: boolean;
  onClose: () => void;
}) {
  return (
    <TgSheet open={open} onClose={onClose} title="Fleet">
      <div className="mb-3 flex flex-wrap gap-1.5">
        {Object.entries(fleet.by_status).map(([status, count]) => (
          <span
            key={status}
            className="rounded-full bg-muted px-2.5 py-1 text-xs tabular-nums text-muted-foreground"
          >
            {status} · {count}
          </span>
        ))}
      </div>
      <ul className="divide-y">
        {fleet.working.map((agent) => (
          <li key={agent.name} className="flex items-center gap-3 py-2.5">
            <TgAvatar name={agent.name} active />
            <div className="min-w-0 flex-1">
              <p className="tg-display text-[13px]">{agent.name}</p>
              <p className="truncate text-xs text-muted-foreground">
                {agent.task_title ??
                  `${agent.role}${agent.team ? ` · ${agent.team}` : ""}`}
              </p>
            </div>
          </li>
        ))}
        {fleet.working.length === 0 && (
          <li className="py-4 text-center text-sm text-muted-foreground">
            No one is mid-task.
          </li>
        )}
      </ul>
    </TgSheet>
  );
}

/**
 * The cockpit home: a spend hero with a live 7-day trend, a quick-action
 * ring, the needs-you banner, the fleet as live avatars, and the week's
 * shipped-task velocity — off the single `/telegram/today` round trip.
 */
export function TgTodayTab({
  onNavigate,
}: {
  /** `intent: "release"` deep-focuses the release proposal in Approvals. */
  onNavigate: (tab: TgTab, intent?: "release") => void;
}) {
  const queryClient = useQueryClient();
  const [fleetOpen, setFleetOpen] = useState(false);
  const [sweepOpen, setSweepOpen] = useState(false);
  const [ackBusy, setAckBusy] = useState(false);
  const [sweepBusy, setSweepBusy] = useState(false);

  // Shares the Inbox tab's query cache; powers the Ack-all badge + action.
  const { data: notifData } = useNotifications();
  const pendingAcks = (notifData?.items ?? []).filter(
    (n) => n.requires_ack && !n.is_acknowledged,
  );

  const runAckAll = async () => {
    haptics.tap();
    if (pendingAcks.length === 0) {
      toast.info("Nothing is waiting for an ack");
      return;
    }
    setAckBusy(true);
    const results = await Promise.allSettled(
      pendingAcks.map((n) => notificationsApi.acknowledge(n.id)),
    );
    const ok = results.filter((r) => r.status === "fulfilled").length;
    await queryClient.invalidateQueries({ queryKey: notificationKeys.all });
    setAckBusy(false);
    if (ok === results.length) {
      haptics.success();
      toast.success(`Acknowledged ${ok} notification${ok === 1 ? "" : "s"}`);
    } else {
      haptics.error();
      toast.warning(`Acknowledged ${ok} of ${results.length}`);
    }
  };

  const runSweep = async () => {
    setSweepBusy(true);
    try {
      const projects = (await projectsApi.list()).filter(
        (p) => p.has_git_token,
      );
      let deleted = 0;
      let errors = 0;
      for (const p of projects) {
        try {
          const res = await gitApi.cleanupBranches({ project_slug: p.slug });
          deleted += res.remote_deleted;
          errors += res.errors;
        } catch {
          errors += 1;
        }
      }
      haptics.success();
      toast.success(
        `Swept ${projects.length} project${projects.length === 1 ? "" : "s"}: ${deleted} stale branch${deleted === 1 ? "" : "es"} deleted${errors ? `, ${errors} error${errors === 1 ? "" : "s"}` : ""}`,
      );
    } catch (err) {
      haptics.error();
      toast.error(getErrorMessage(err));
    } finally {
      setSweepBusy(false);
      setSweepOpen(false);
    }
  };
  const { data, isLoading, isError } = useQuery<TodayBrief>({
    queryKey: ["tg-today"],
    queryFn: async () => {
      if (isTgDemoMode()) {
        return (await import("@/lib/telegram/demo-data")).DEMO_TODAY;
      }
      return (await api.get<TodayBrief>("/telegram/today")).data;
    },
    refetchInterval: REFETCH_MS,
  });

  const { lastMessage } = useWebSocket<{ type?: string }>("/system");
  useEffect(() => {
    if (lastMessage?.type !== "USAGE_SNAPSHOT") return;
    void queryClient.invalidateQueries({ queryKey: ["tg-today"] });
  }, [lastMessage, queryClient]);

  if (isLoading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-32 w-full rounded-2xl" />
        <Skeleton className="h-16 w-full rounded-2xl" />
        <Skeleton className="h-24 w-full rounded-2xl" />
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="flex flex-col items-center gap-2 py-10 text-center text-muted-foreground">
        <AlertTriangle className="h-8 w-8 opacity-50" />
        <p className="text-sm">Couldn&apos;t load the brief</p>
      </div>
    );
  }

  const { needs_you: needs, fleet, spend, velocity, ship } = data;
  const go = (tab: TgTab, intent?: "release") => {
    haptics.tap();
    if (intent) onNavigate(tab, intent);
    else onNavigate(tab);
  };
  const idle = fleet.by_status.idle ?? 0;
  const active = fleet.by_status.active ?? Math.max(fleet.working.length, 0);

  return (
    <div className="tg-stagger space-y-3">
      <header className="flex items-center justify-between px-1 pt-0.5">
        <p className="tg-display text-[13px] tracking-[0.24em] text-muted-foreground">
          ROBOCO<span className="tg-cursor text-primary">_</span>
        </p>
      </header>
      <SpendHero spend={spend} />

      {/* Operations, not navigation — the tab bar already navigates. */}
      <div className="flex items-stretch gap-2 px-1">
        <TgCircleAction
          icon={IconShip}
          label="Ship"
          badge={ship.open_release_proposal ? 1 : undefined}
          accent={ship.open_release_proposal}
          onPress={() => go("approvals", "release")}
        />
        <TgCircleAction
          icon={IconAckAll}
          label="Ack all"
          badge={pendingAcks.length}
          busy={ackBusy}
          onPress={() => void runAckAll()}
        />
        <TgCircleAction
          icon={IconSweep}
          label="Sweep"
          busy={sweepBusy}
          onPress={() => {
            haptics.tap();
            setSweepOpen(true);
          }}
        />
        <TgCircleAction
          icon={IconFleet}
          label="Fleet"
          onPress={() => {
            haptics.tap();
            setFleetOpen(true);
          }}
        />
      </div>

      <NeedsYouBanner
        needs={needs}
        onApprovals={() => go("approvals")}
        onBoard={() => go("board")}
      />

      <TgSection
        title="Fleet"
        trailing={
          <span className="text-[11px] tabular-nums text-muted-foreground">
            {active} active · {idle} idle
          </span>
        }
      >
        {fleet.working.length === 0 ? (
          <p className="py-1 text-sm text-muted-foreground">
            No one is mid-task.
          </p>
        ) : (
          <button
            type="button"
            onClick={() => {
              haptics.tap();
              setFleetOpen(true);
            }}
            className="w-full space-y-2 text-left"
          >
            <div className="flex -space-x-1.5 overflow-hidden">
              {fleet.working.map((a) => (
                <TgAvatar key={a.name} name={a.name} active />
              ))}
            </div>
            <ul className="space-y-1">
              {fleet.working.slice(0, 3).map((agent) => (
                <li
                  key={agent.name}
                  className="flex items-baseline gap-2 text-[13px] leading-snug"
                >
                  <span className="tg-display shrink-0 text-xs">
                    {agent.name}
                  </span>
                  {agent.task_title && (
                    <span className="truncate text-muted-foreground">
                      {agent.task_title}
                    </span>
                  )}
                </li>
              ))}
            </ul>
            {fleet.working.length > 3 && (
              <p className="text-[11px] text-muted-foreground">
                +{fleet.working.length - 3} more · tap for the full roster
              </p>
            )}
          </button>
        )}
      </TgSection>

      <div className="grid grid-cols-2 gap-2.5">
        <TgSection
          title="Shipped this week"
          trailing={
            <span className="text-sm font-semibold tabular-nums text-foreground">
              {velocity.week_total}
            </span>
          }
        >
          <DayBars
            values={velocity.series}
            labels={weekdayLabels(velocity.series.length)}
          />
        </TgSection>
        <TgSection icon={Rocket} title="Ship">
          <button
            type="button"
            onClick={() => ship.open_release_proposal && go("approvals")}
            className="w-full text-left"
          >
            <p
              className={cn(
                "tg-display text-[22px] leading-tight tabular-nums",
                ship.open_release_proposal && "text-primary",
              )}
            >
              v{ship.version}
            </p>
            <p className="mt-0.5 text-[11px] leading-tight text-muted-foreground">
              {ship.open_release_proposal
                ? "Release proposal waiting"
                : ship.ci_fix_tasks > 0
                  ? `${ship.ci_fix_tasks} CI fix open`
                  : "No release pending"}
            </p>
          </button>
        </TgSection>
      </div>

      <FleetSheet
        fleet={fleet}
        open={fleetOpen}
        onClose={() => setFleetOpen(false)}
      />

      <TgSheet
        open={sweepOpen}
        onClose={() => {
          if (!sweepBusy) setSweepOpen(false);
        }}
        title="Sweep branches"
      >
        <div className="space-y-3 pb-1">
          <p className="text-sm leading-relaxed text-muted-foreground">
            Deletes the remote and local branches of every completed or
            cancelled task, across every project with git configured. Live
            branches and environment rungs are spared.
          </p>
          <Button
            className="w-full"
            disabled={sweepBusy}
            onClick={() => void runSweep()}
          >
            {sweepBusy ? "Sweeping…" : "Sweep stale branches"}
          </Button>
        </div>
      </TgSheet>
    </div>
  );
}
