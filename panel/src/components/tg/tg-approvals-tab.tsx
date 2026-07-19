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
import { useBackButton, useTgWebApp } from "@/lib/telegram/hooks";
import { haptics } from "@/lib/telegram/webapp";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { TgRow, TgRowIcon } from "@/components/tg/ui";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Clapperboard,
  Map as MapIcon,
  MessageCircle,
  Rocket,
} from "lucide-react";

const KIND_META: Record<
  ApprovalItem["kind"],
  { label: string; icon: typeof Rocket; tone: string }
> = {
  release: { label: "Release", icon: Rocket, tone: "amber" },
  x_post: { label: "X post", icon: MessageCircle, tone: "sky" },
  video_post: { label: "Video", icon: Clapperboard, tone: "violet" },
  roadmap: { label: "Roadmap", icon: MapIcon, tone: "emerald" },
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
  }
}

function ItemRow({
  item,
  onOpen,
}: {
  item: ApprovalItem;
  onOpen: () => void;
}) {
  const meta = KIND_META[item.kind];
  return (
    <div className="rounded-xl border bg-card text-card-foreground">
      <TgRow
        leading={<TgRowIcon icon={meta.icon} tone={meta.tone} />}
        title={itemTitle(item)}
        lines={2}
        meta={meta.label}
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
  }
}

/**
 * The approvals card stack: every held draft across the four queues as one
 * list; tapping focuses a full-context detail whose primary action rides
 * Telegram's MainButton and whose back navigation rides the native
 * BackButton (with visible fallbacks outside Telegram). An acted-on item
 * vanishes from the refetched queue, which pops the view back to the list
 * by construction.
 */
export function TgApprovalsTab() {
  const { items, isLoading, anyFailed } = useApprovalQueue();
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const webApp = useTgWebApp();

  const focused = items.find((i) => i.id === focusedId) ?? null;
  const back = () => setFocusedId(null);
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
        <AlertTriangle className="h-8 w-8 opacity-50" />
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
            <Button variant="ghost" size="sm" className="-ml-1.5 px-1.5" onClick={back}>
              <ArrowLeft className="h-4 w-4" />
            </Button>
          )}
          <meta.icon className="h-3.5 w-3.5 text-muted-foreground" />
          <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
            {meta.label}
          </p>
        </div>
        <Detail item={focused} onDone={back} />
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 py-10 text-center text-muted-foreground">
        <CheckCircle2 className="h-8 w-8 opacity-50" />
        <p className="text-sm">Queue is clear</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {anyFailed && (
        <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <AlertTriangle className="h-3.5 w-3.5" />
          Some queues couldn&apos;t load — this list may be incomplete.
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
