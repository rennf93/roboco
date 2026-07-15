"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { videoApi } from "@/lib/api";
import type { VideoPipelineItem } from "@/lib/api/video";
import {
  derivePipelineStage,
  pipelineStageColor,
  pipelineStageLabel,
  type PipelineStage,
} from "./video-pipeline-utils";
import { RerenderControl } from "@/components/dashboard/video-rerender-control";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { HelpTip } from "@/components/ui/help-tip";
import { Film } from "lucide-react";

// Per-stage explanation for the stage chip — lives here (not in
// video-pipeline-utils.ts) since that file is pure derivation logic with its
// own dedicated unit tests.
function stageHint(stage: PipelineStage): string {
  switch (stage.kind) {
    case "authoring":
      return "A developer is building this video's composition";
    case "in_review":
      return "The authoring PR is in QA / PR / PM review before assembly";
    case "awaiting_approval":
      return "Rendered and waiting on your approval — open the task to review";
    case "rendering":
      return "The renderer is producing the 9:16 and 1:1 cuts, retrying on failure";
    case "render_failed":
      return "The renderer gave up after all retries — re-render to try again";
  }
}

// One row: title + occasion + a colored stage chip. The stage chip is
// derived (never fetched) from status + render_status/render_attempts —
// see video-pipeline-utils.ts, unit-tested directly there. Only the
// "awaiting your approval" stage deep-links out (the CEO decision the
// pipeline is surfacing); every other stage is just visibility. The
// re-render control (shared with video-post-queue.tsx) shows for any item
// carrying a proposed composition — the "rendering" and "render_failed"
// stages are the only ones where the item's own task_id doubles as the
// authoring task id the rerender endpoint needs, regardless of whether the
// render is currently retrying or has failed outright.
function PipelineRow({ item }: { item: VideoPipelineItem }) {
  const stage = derivePipelineStage(item);
  const canRerender =
    (stage.kind === "rendering" || stage.kind === "render_failed") &&
    !!item.composition_id;
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border p-3 text-sm">
      <Film className="h-4 w-4 shrink-0 text-muted-foreground" />
      <span className="font-medium">{item.title}</span>
      {item.occasion && (
        <HelpTip label="The occasion/event this video was drafted for">
          <Badge variant="outline">{item.occasion}</Badge>
        </HelpTip>
      )}
      <HelpTip label={stageHint(stage)}>
        <Badge className={`${pipelineStageColor(stage)} text-white`}>
          {pipelineStageLabel(stage)}
        </Badge>
      </HelpTip>
      {stage.kind === "awaiting_approval" && (
        <Link
          href={`/tasks/${item.task_id}`}
          prefetch={false}
          className="ml-auto"
        >
          <Button variant="outline" size="sm">
            Review
          </Button>
        </Link>
      )}
      {canRerender && (
        <div className="ml-auto">
          <RerenderControl authoringTaskId={item.task_id} />
        </div>
      )}
    </div>
  );
}

// Social page visibility strip: every source=video item still moving
// through authoring/review/rendering, above the Video Post Queue. Renders
// nothing (not even an empty-state card) when the pipeline is empty — the
// queue's own state-aware copy already covers "nothing in flight". Polls
// on the same 30s cadence as the queue.
export function VideoPipelineStrip({ className }: { className?: string }) {
  const { data: items, isLoading } = useQuery({
    queryKey: ["video", "pipeline"],
    queryFn: () => videoApi.listPipeline(),
    refetchInterval: 30000,
  });

  if (isLoading || !items || items.length === 0) return null;

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Film className="h-5 w-5" />
          Video Pipeline
          <Badge variant="secondary">{items.length}</Badge>
        </CardTitle>
        <CardDescription>
          Every video in flight — authoring, review, or rendering — before it
          lands in the queue below.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        {items.map((item) => (
          <PipelineRow key={item.task_id} item={item} />
        ))}
      </CardContent>
    </Card>
  );
}
