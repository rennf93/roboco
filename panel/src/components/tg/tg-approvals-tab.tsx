"use client";

import { useState } from "react";
import {
  useApprovalQueue,
  type ApprovalItem,
} from "@/components/tg/approvals/use-approval-queue";
import { ReleaseDetail } from "@/components/tg/approvals/release-detail";
import { XPostDetail } from "@/components/tg/approvals/x-post-detail";
import { VideoPostDetail } from "@/components/tg/approvals/video-post-detail";
import { RoadmapItemDetail } from "@/components/tg/approvals/roadmap-item-detail";
import {
  BoardProgramItemDetail,
  ScalesItemDetail,
} from "@/components/tg/approvals/board-program-detail";
import { useBackButton, useTgWebApp } from "@/lib/telegram/hooks";
import { haptics } from "@/lib/telegram/webapp";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { TgRow, TgRowIcon, TG_CARD } from "@/components/tg/ui";
import { cn } from "@/lib/utils";
import { ArrowLeft } from "@phosphor-icons/react";
import {
  Bug,
  ChatCircle,
  CheckCircle,
  Dog,
  FilmSlate,
  MapTrifold,
  RocketLaunch,
  Scales,
  Wrench,
  Warning,
} from "@phosphor-icons/react";

const KIND_META: Record<
  ApprovalItem["kind"],
  { label: string; icon: typeof RocketLaunch; tone: string }
> = {
  release: { label: "Release", icon: RocketLaunch, tone: "amber" },
  x_post: { label: "X post", icon: ChatCircle, tone: "sky" },
  video_post: { label: "Video", icon: FilmSlate, tone: "violet" },
  roadmap: { label: "Roadmap", icon: MapTrifold, tone: "emerald" },
  pest_control: { label: "Pest Control", icon: Bug, tone: "rose" },
  spackle: { label: "Spackle", icon: Wrench, tone: "amber" },
  dogfood: { label: "Dogfood", icon: Dog, tone: "sky" },
  scales: { label: "Scales", icon: Scales, tone: "emerald" },
};

function itemTitle(item: ApprovalItem): string {
  switch (item.kind) {
    case "release":
      return `Release v${item.proposal.report.proposed_version}`;
    case "x_post":
      return item.post.body;
    case "video_post":
      return item.post.title;
    case "roadmap":
      return item.item.title;
    case "scales":
      return item.item.target_task_title;
    case "pest_control":
    case "spackle":
    case "dogfood":
      return item.item.title;
  }
}

/** Short evidence line for the board-program rows — the desktop cards lead
 * with the same evidence summary. */
function itemSubtitle(item: ApprovalItem): string | undefined {
  switch (item.kind) {
    case "pest_control":
    case "spackle":
    case "dogfood":
      return item.item.evidence;
    case "scales":
      return item.item.rationale;
    case "release":
    case "x_post":
    case "video_post":
    case "roadmap":
      return undefined;
  }
  const _exhaustive: never = item;
  return _exhaustive;
}

function ItemRow({ item, onOpen }: { item: ApprovalItem; onOpen: () => void }) {
  const meta = KIND_META[item.kind];
  const subtitle = itemSubtitle(item);
  return (
    <div className={cn(TG_CARD, "px-2 py-1 text-card-foreground")}>
      <TgRow
        leading={<TgRowIcon icon={meta.icon} tone={meta.tone} />}
        title={itemTitle(item)}
        lines={2}
        meta={subtitle ? `${meta.label} · ${subtitle}` : meta.label}
        onPress={onOpen}
      />
    </div>
  );
}

function Detail({ item, onDone }: { item: ApprovalItem; onDone: () => void }) {
  switch (item.kind) {
    case "release":
      return <ReleaseDetail proposal={item.proposal} onDone={onDone} />;
    case "x_post":
      return <XPostDetail post={item.post} onDone={onDone} />;
    case "video_post":
      return <VideoPostDetail post={item.post} onDone={onDone} />;
    case "roadmap":
      return (
        <RoadmapItemDetail
          cycleId={item.cycleId}
          item={item.item}
          onDone={onDone}
        />
      );
    case "scales":
      return (
        <ScalesItemDetail
          cycleId={item.cycleId}
          item={item.item}
          onDone={onDone}
        />
      );
    case "pest_control":
    case "spackle":
    case "dogfood":
      return (
        <BoardProgramItemDetail
          kind={item.kind}
          cycleId={item.cycleId}
          item={item.item}
          onDone={onDone}
        />
      );
  }
  const _exhaustive: never = item;
  return _exhaustive;
}

/**
 * The approvals card stack: one list across the eight queues it sources
 * (release, X post, video, roadmap, and the four board-program queues);
 * tapping focuses a full-context detail whose primary
 * action rides Telegram's MainButton and whose back navigation rides the
 * native BackButton (with visible fallbacks outside Telegram). An acted-on
 * item vanishes from the refetched queue, which pops the view back to the
 * list by construction.
 */
export function TgApprovalsTab({
  initialFocus,
}: {
  /** Auto-focus the first item of this kind once — Today's Ship deep link. */
  initialFocus?: "release";
} = {}) {
  const { items, isLoading, anyFailed } = useApprovalQueue();
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const [initialConsumed, setInitialConsumed] = useState(false);
  const webApp = useTgWebApp();

  // Derived, not an effect: the deep link focuses the first matching item
  // until the user backs out of it once.
  const autoTarget =
    initialFocus && !initialConsumed && focusedId === null
      ? items.find((i) => i.kind === initialFocus)
      : undefined;
  const focused = items.find((i) => i.id === focusedId) ?? autoTarget ?? null;
  const back = () => {
    setInitialConsumed(true);
    setFocusedId(null);
  };
  useBackButton(focused ? back : null);

  if (isLoading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-16 w-full" />
        ))}
      </div>
    );
  }

  if (anyFailed && items.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 py-10 text-center text-muted-foreground">
        <Warning className="h-8 w-8 opacity-50" />
        <p className="text-sm">Couldn&apos;t load the queues</p>
      </div>
    );
  }

  if (focused) {
    const meta = KIND_META[focused.kind];
    return (
      <div className="space-y-3">
        <div className="flex items-center gap-1.5">
          {!webApp?.BackButton && (
            <Button
              variant="ghost"
              size="sm"
              className="-ml-1.5 px-1.5"
              onClick={back}
            >
              <ArrowLeft className="h-4 w-4" />
            </Button>
          )}
          <span className="inline-flex items-center gap-1.5 rounded-full bg-muted/60 px-2.5 py-1 text-xs font-medium text-muted-foreground">
            <meta.icon className="h-3.5 w-3.5" />
            {meta.label}
          </span>
        </div>
        <Detail item={focused} onDone={back} />
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 py-10 text-center text-muted-foreground">
        <CheckCircle className="h-8 w-8 opacity-50" />
        <p className="text-sm">Queue is clear</p>
      </div>
    );
  }

  return (
    <div className="tg-stagger space-y-2">
      <p className="px-1 text-[13px] font-semibold text-foreground/90">
        {items.length} waiting for you
      </p>
      {anyFailed && (
        <p className="flex items-center gap-1.5 rounded-2xl bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
          <Warning className="h-3.5 w-3.5 shrink-0" />
          Some queues didn&apos;t load, so this list may be incomplete.
        </p>
      )}
      {items.map((item) => (
        <ItemRow
          key={item.id}
          item={item}
          onOpen={() => {
            haptics.tap();
            setFocusedId(item.id);
          }}
        />
      ))}
    </div>
  );
}
