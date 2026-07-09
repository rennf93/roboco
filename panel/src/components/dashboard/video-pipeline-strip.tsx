"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { videoApi } from "@/lib/api";
import type { VideoPipelineItem } from "@/lib/api/video";
import {
  derivePipelineStage,
  pipelineStageColor,
  pipelineStageLabel,
} from "./video-pipeline-utils";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Film } from "lucide-react";

// One row: title + occasion + a colored stage chip. The stage chip is
// derived (never fetched) from status + render_status/render_attempts —
// see video-pipeline-utils.ts, unit-tested directly there. Only the
// "awaiting your approval" stage deep-links out (the CEO decision the
// pipeline is surfacing); every other stage is just visibility.
function PipelineRow({ item }: { item: VideoPipelineItem }) {
  const stage = derivePipelineStage(item);
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border p-3 text-sm">
      <Film className="h-4 w-4 shrink-0 text-muted-foreground" />
      <span className="font-medium">{item.title}</span>
      {item.occasion && <Badge variant="outline">{item.occasion}</Badge>}
      <Badge className={`${pipelineStageColor(stage)} text-white`}>
        {pipelineStageLabel(stage)}
      </Badge>
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
