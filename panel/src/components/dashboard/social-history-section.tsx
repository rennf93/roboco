"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { xApi, videoApi } from "@/lib/api";
import type { XPostHistoryEntry } from "@/lib/api/x";
import type { VideoPostHistoryEntry } from "@/lib/api/video";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  AtSign,
  ChevronDown,
  Film,
  History,
  Rocket,
  Sparkles,
} from "lucide-react";

const HISTORY_LIMIT = 50;
const PLATFORM_LABELS: Record<string, string> = { x: "X", tiktok: "TikTok" };

type UnifiedRow =
  | { kind: "x"; entry: XPostHistoryEntry }
  | { kind: "video"; entry: VideoPostHistoryEntry };

function xKindMeta(source: XPostHistoryEntry["source"]) {
  if (source === "x_post") return { label: "X post", icon: Rocket };
  if (source === "x_feature")
    return { label: "Feature spotlight", icon: Sparkles };
  return { label: "X reply", icon: AtSign };
}

// One unified row: X entries link the posted tweet / show the reject reason;
// video entries list each platform's posted id (X id links out, TikTok's
// inbox upload has no public URL so just the raw id is shown).
function UnifiedHistoryRow({ row }: { row: UnifiedRow }) {
  const posted = row.entry.status === "completed";
  const statusBadge = posted ? (
    <Badge className="bg-green-600 hover:bg-green-600">Posted</Badge>
  ) : (
    <Badge variant="destructive">Rejected</Badge>
  );
  const timestamp = (
    <span className="ml-auto text-xs text-muted-foreground">
      {new Date(row.entry.acted_at).toLocaleString()}
    </span>
  );

  if (row.kind === "x") {
    const meta = xKindMeta(row.entry.source);
    return (
      <div className="rounded-lg border p-3 text-sm">
        <div className="mb-1 flex flex-wrap items-center gap-2">
          <meta.icon className="h-4 w-4 text-muted-foreground" />
          <span className="font-medium">{meta.label}</span>
          {statusBadge}
          {timestamp}
        </div>
        <p className="line-clamp-2 text-muted-foreground">{row.entry.body}</p>
        {posted && row.entry.tweet_id && (
          <a
            href={`https://x.com/i/status/${row.entry.tweet_id}`}
            target="_blank"
            rel="noreferrer"
            className="text-xs text-primary underline"
          >
            View on X
          </a>
        )}
        {!posted && row.entry.reject_reason && (
          <p className="mt-1 text-xs text-muted-foreground">
            Reason: {row.entry.reject_reason}
          </p>
        )}
      </div>
    );
  }

  const platformIds = Object.entries(row.entry.posted);
  return (
    <div className="rounded-lg border p-3 text-sm">
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <Film className="h-4 w-4 text-muted-foreground" />
        <span className="font-medium">Video</span>
        {row.entry.occasion && (
          <Badge variant="outline">{row.entry.occasion}</Badge>
        )}
        {statusBadge}
        {timestamp}
      </div>
      {posted && platformIds.length > 0 && (
        <div className="flex flex-wrap gap-3 text-xs">
          {platformIds.map(([platform, id]) =>
            platform === "x" ? (
              <a
                key={platform}
                href={`https://x.com/i/status/${id}`}
                target="_blank"
                rel="noreferrer"
                className="text-primary underline"
              >
                {PLATFORM_LABELS[platform] ?? platform}: {id}
              </a>
            ) : (
              <span key={platform} className="text-muted-foreground">
                {PLATFORM_LABELS[platform] ?? platform}: {id}
              </span>
            ),
          )}
        </div>
      )}
      {!posted && row.entry.reject_reason && (
        <p className="mt-1 text-xs text-muted-foreground">
          Reason: {row.entry.reject_reason}
        </p>
      )}
    </div>
  );
}

// Unified, collapsed-by-default history across X and video — merged client-
// side, newest-acted first. Lazy-fetched only once expanded (both sources in
// parallel); fixed 50-row-per-source limit (server default), no "show more".
export function SocialHistorySection({ className }: { className?: string }) {
  const [open, setOpen] = useState(false);

  const { data: xHistory, isLoading: xLoading } = useQuery({
    queryKey: ["x", "posts", "history"],
    queryFn: () => xApi.listHistory(HISTORY_LIMIT),
    enabled: open,
  });
  const { data: videoHistory, isLoading: videoLoading } = useQuery({
    queryKey: ["video", "posts", "history"],
    queryFn: () => videoApi.listHistory(HISTORY_LIMIT),
    enabled: open,
  });

  const isLoading = open && (xLoading || videoLoading);
  const rows: UnifiedRow[] = [
    ...(xHistory ?? []).map((entry): UnifiedRow => ({ kind: "x", entry })),
    ...(videoHistory ?? []).map(
      (entry): UnifiedRow => ({
        kind: "video",
        entry,
      }),
    ),
  ].sort(
    (a, b) =>
      new Date(b.entry.acted_at).getTime() -
      new Date(a.entry.acted_at).getTime(),
  );

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <History className="h-5 w-5" />
          History
        </CardTitle>
        <CardDescription>
          Every posted or rejected draft across X and video — newest first.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Collapsible open={open} onOpenChange={setOpen}>
          <CollapsibleTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              className="w-full justify-between"
            >
              <span>{open ? "Hide" : "Show"} history</span>
              <ChevronDown
                className={`h-4 w-4 transition-transform ${open ? "rotate-180" : ""}`}
              />
            </Button>
          </CollapsibleTrigger>
          <CollapsibleContent className="space-y-2 pt-2">
            {isLoading && (
              <p className="text-sm text-muted-foreground">Loading...</p>
            )}
            {!isLoading && rows.length === 0 && (
              <p className="text-sm text-muted-foreground">
                No acted-on drafts yet.
              </p>
            )}
            {rows.map((row) => (
              <UnifiedHistoryRow
                key={`${row.kind}-${row.entry.task_id}`}
                row={row}
              />
            ))}
          </CollapsibleContent>
        </Collapsible>
      </CardContent>
    </Card>
  );
}
