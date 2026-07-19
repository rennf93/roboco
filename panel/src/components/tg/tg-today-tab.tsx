"use client";

import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api/client";
import { isTgDemoMode } from "@/lib/telegram/demo";
import { useWebSocket } from "@/hooks/use-websocket";
import { haptics } from "@/lib/telegram/webapp";
import type { TgTab } from "@/components/tg/tg-tab-bar";
import { Skeleton } from "@/components/ui/skeleton";
import { TgRow, TgSection, TgStat } from "@/components/tg/ui";
import {
  AlertTriangle,
  CheckCircle2,
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

function taskMeta(task: TodayTaskItem): string {
  const parts = [task.team ?? "—"];
  if (task.updated_at) {
    parts.push(`${formatDistanceToNow(new Date(task.updated_at))} ago`);
  }
  return parts.join(" · ");
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
    <div className="space-y-2.5">
      <TgSection
        icon={CheckCircle2}
        title="Needs you"
        trailing={
          needs.total > 0 ? (
            <span className="rounded-full bg-primary px-2 py-0.5 text-[11px] font-semibold tabular-nums text-primary-foreground">
              {needs.total}
            </span>
          ) : undefined
        }
      >
        {needs.total === 0 ? (
          <p className="py-1.5 text-sm text-muted-foreground">
            All clear — nothing is waiting on you.
          </p>
        ) : (
          <div className="space-y-1.5">
            {heldEntries.length > 0 && (
              <div className="flex flex-wrap gap-1.5 pb-0.5">
                {heldEntries.map(([key, count]) => (
                  <button
                    key={key}
                    type="button"
                    onClick={() => go("approvals")}
                    className="rounded-full bg-primary/10 px-2.5 py-1 text-xs font-medium tabular-nums text-primary transition-colors active:bg-primary/20"
                  >
                    {DRAFT_LABELS[key] ?? key} · {count}
                  </button>
                ))}
              </div>
            )}
            <div className="-mx-1.5 divide-y divide-border/60">
              {needs.awaiting_ceo.map((t) => (
                <TgRow
                  key={t.id}
                  title={t.title}
                  meta={taskMeta(t)}
                  onPress={() => go("board")}
                />
              ))}
              {needs.blocked.map((t) => (
                <TgRow
                  key={t.id}
                  title={t.title}
                  meta={
                    <>
                      <span className="font-medium text-destructive">
                        blocked
                      </span>
                      {" · "}
                      {taskMeta(t)}
                    </>
                  }
                  onPress={() => go("board")}
                />
              ))}
            </div>
          </div>
        )}
      </TgSection>

      <TgSection
        icon={Users}
        title="Fleet"
        trailing={
          <span className="text-[11px] tabular-nums text-muted-foreground">
            {fleet.total} agents
            {Object.entries(fleet.by_status).map(
              ([status, count]) => ` · ${count} ${status}`,
            )}
          </span>
        }
      >
        {fleet.working.length === 0 ? (
          <p className="py-1 text-sm text-muted-foreground">
            No one is mid-task.
          </p>
        ) : (
          <ul className="space-y-1.5">
            {fleet.working.map((agent) => (
              <li
                key={agent.name}
                className="flex items-baseline gap-2 text-[13px] leading-snug"
              >
                <span className="shrink-0 font-mono text-xs font-medium">
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
        )}
      </TgSection>

      <div className="grid grid-cols-2 gap-2.5">
        <TgSection icon={CircleDollarSign} title="Spend today">
          <TgStat
            value={`$${spend.cost_today_usd.toFixed(2)}`}
            caption={`${compactNumber.format(spend.tokens_today)} tokens`}
          />
        </TgSection>
        <TgSection icon={Rocket} title="Ship">
          <TgStat
            value={`v${ship.version}`}
            tone={ship.open_release_proposal ? "attention" : "default"}
            caption={
              ship.open_release_proposal
                ? "Release proposal waiting"
                : ship.ci_fix_tasks > 0
                  ? `${ship.ci_fix_tasks} CI fix open`
                  : "No release pending"
            }
          />
        </TgSection>
      </div>
    </div>
  );
}
