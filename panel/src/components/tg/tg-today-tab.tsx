"use client";

import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api/client";
import { isTgDemoMode } from "@/lib/telegram/demo";
import { useWebSocket } from "@/hooks/use-websocket";
import { haptics } from "@/lib/telegram/webapp";
import type { TgTab } from "@/components/tg/tg-tab-bar";
import { Skeleton } from "@/components/ui/skeleton";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  CircleDollarSign,
  Rocket,
  Users,
} from "lucide-react";
import { formatDistanceToNow } from "date-fns";

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
  spend: { tokens_today: number; cost_today_usd: number };
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

const compactNumber = new Intl.NumberFormat("en", {
  notation: "compact",
  maximumFractionDigits: 1,
});

function SectionCard({
  icon: Icon,
  title,
  children,
}: {
  icon: typeof Users;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border bg-card p-3 text-card-foreground">
      <h2 className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        <Icon className="h-3.5 w-3.5" />
        {title}
      </h2>
      {children}
    </section>
  );
}

function TaskRow({
  task,
  onOpen,
}: {
  task: TodayTaskItem;
  onOpen: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className="flex w-full items-center gap-2 rounded-md py-1.5 text-left"
    >
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm leading-snug">{task.title}</p>
        <p className="text-[11px] text-muted-foreground">
          {task.team ?? "—"}
          {task.updated_at &&
            ` · ${formatDistanceToNow(new Date(task.updated_at))} ago`}
        </p>
      </div>
      <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
    </button>
  );
}

/**
 * The cockpit's home screen: one glance answering "does anything need me?"
 * — capped needs-you items, held-draft counts, fleet, today's spend, and
 * ship state, off the single aggregated `/telegram/today` round trip.
 * Row taps deep-link into the tab that acts on the item.
 */
export function TgTodayTab({
  onNavigate,
}: {
  onNavigate: (tab: TgTab) => void;
}) {
  const queryClient = useQueryClient();
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

  // Rides the shared /ws/system socket (ref-counted — no extra connection):
  // each USAGE_SNAPSHOT push refreshes the brief so the spend line tracks
  // the sweeper live; the poll above stays as the socket-down fallback.
  const { lastMessage } = useWebSocket<{ type?: string }>("/system");
  useEffect(() => {
    if (lastMessage?.type !== "USAGE_SNAPSHOT") return;
    void queryClient.invalidateQueries({ queryKey: ["tg-today"] });
  }, [lastMessage, queryClient]);

  if (isLoading) {
    return (
      <div className="space-y-3">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-24 w-full" />
        ))}
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

  const { needs_you: needs, fleet, spend, ship } = data;
  const go = (tab: TgTab) => {
    haptics.tap();
    onNavigate(tab);
  };
  const heldEntries = Object.entries(needs.held_drafts).filter(
    ([, count]) => count > 0,
  );

  return (
    <div className="space-y-3">
      <SectionCard icon={CheckCircle2} title="Needs you">
        {needs.total === 0 ? (
          <p className="py-2 text-sm text-muted-foreground">
            All clear — nothing is waiting on you.
          </p>
        ) : (
          <div className="space-y-2">
            {heldEntries.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {heldEntries.map(([key, count]) => (
                  <button
                    key={key}
                    type="button"
                    onClick={() => go("approvals")}
                    className="rounded-full bg-primary/10 px-2.5 py-1 text-xs font-medium text-primary"
                  >
                    {DRAFT_LABELS[key] ?? key} · {count}
                  </button>
                ))}
              </div>
            )}
            {needs.awaiting_ceo.map((t) => (
              <TaskRow key={t.id} task={t} onOpen={() => go("board")} />
            ))}
            {needs.blocked_count > 0 && (
              <p className="text-[11px] font-medium text-destructive">
                {needs.blocked_count} blocked
              </p>
            )}
            {needs.blocked.map((t) => (
              <TaskRow key={t.id} task={t} onOpen={() => go("board")} />
            ))}
          </div>
        )}
      </SectionCard>

      <SectionCard icon={Users} title="Fleet">
        <div className="mb-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
          <span>{fleet.total} agents</span>
          {Object.entries(fleet.by_status).map(([status, count]) => (
            <span key={status}>
              {count} {status}
            </span>
          ))}
        </div>
        {fleet.working.length === 0 ? (
          <p className="text-sm text-muted-foreground">No one is mid-task.</p>
        ) : (
          <ul className="space-y-1">
            {fleet.working.map((agent) => (
              <li key={agent.name} className="text-sm leading-snug">
                <span className="font-medium">{agent.name}</span>
                {agent.task_title && (
                  <span className="text-muted-foreground">
                    {" "}
                    — {agent.task_title}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </SectionCard>

      <div className="grid grid-cols-2 gap-3">
        <SectionCard icon={CircleDollarSign} title="Spend today">
          <p className="text-lg font-semibold tabular-nums">
            ${spend.cost_today_usd.toFixed(2)}
          </p>
          <p className="text-xs text-muted-foreground tabular-nums">
            {compactNumber.format(spend.tokens_today)} tokens
          </p>
        </SectionCard>
        <SectionCard icon={Rocket} title="Ship">
          <p className="text-lg font-semibold tabular-nums">v{ship.version}</p>
          <p className="text-xs text-muted-foreground">
            {ship.open_release_proposal
              ? "Release proposal waiting"
              : ship.ci_fix_tasks > 0
                ? `${ship.ci_fix_tasks} CI fix open`
                : "No release pending"}
          </p>
        </SectionCard>
      </div>
    </div>
  );
}
