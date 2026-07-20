"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { videoApi } from "@/lib/api";
import type { PreviewFrame, VideoCut } from "@/lib/api/video";
import { Task } from "@/types";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { HelpTip } from "@/components/ui/help-tip";
import { formatAbsoluteTimestamp } from "@/lib/utils";
import { ChevronLeft, ChevronRight, Clapperboard } from "lucide-react";

const CUT_LABELS: Record<VideoCut, string> = {
  vertical: "9:16",
  square: "1:1",
};

// One orientation's frame strip: blob-fetches the current frame (same
// auth-header workaround as the MP4 CutPlayer) and steps through the rest
// with a native range input — no scrubber library needed for N still frames.
// The caller remounts this via `key={cut}` on cut change, so `index`/`src`
// reset for free — no effect needed to clamp a stale index back to 0.
function FrameStepper({
  taskId,
  cut,
  frames,
}: {
  taskId: string;
  cut: VideoCut;
  frames: PreviewFrame[];
}) {
  const [index, setIndex] = useState(0);
  const [src, setSrc] = useState<string | null>(null);
  const frame: PreviewFrame | undefined = frames[index];

  useEffect(() => {
    if (!frame) return; // src stays null (its initial value) — nothing to fetch
    let cancelled = false;
    let objectUrl: string | null = null;
    videoApi
      .getPreviewFrameBlob(taskId, cut, frame.file)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setSrc(objectUrl);
      })
      .catch(() => {
        if (!cancelled) setSrc(null);
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [taskId, cut, frame]);

  if (frames.length === 0) {
    return (
      <div className="flex h-48 items-center justify-center rounded-md border border-dashed text-sm text-muted-foreground">
        This cut hasn&apos;t rendered a preview yet
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {src ? (
        <img
          src={src}
          alt={`Frame ${frame?.index} of ${cut} cut, at ${frame?.timestamp_seconds}s`}
          className="mx-auto max-h-96 w-full rounded-md border bg-black object-contain"
        />
      ) : (
        <Skeleton className="mx-auto h-96 w-full rounded-md" />
      )}
      <div className="flex items-center gap-2">
        <Button
          type="button"
          size="icon"
          variant="outline"
          disabled={index === 0}
          onClick={() => setIndex((i) => Math.max(0, i - 1))}
          aria-label="Previous frame"
        >
          <ChevronLeft className="h-4 w-4" />
        </Button>
        <input
          type="range"
          aria-label="Frame scrubber"
          min={0}
          max={frames.length - 1}
          value={index}
          onChange={(e) => setIndex(Number(e.target.value))}
          className="w-full"
        />
        <Button
          type="button"
          size="icon"
          variant="outline"
          disabled={index === frames.length - 1}
          onClick={() => setIndex((i) => Math.min(frames.length - 1, i + 1))}
          aria-label="Next frame"
        >
          <ChevronRight className="h-4 w-4" />
        </Button>
      </div>
      <p className="text-center text-xs text-muted-foreground">
        Frame {index + 1}/{frames.length} —{" "}
        {frame?.timestamp_seconds.toFixed(1)}s into the clip
      </p>
    </div>
  );
}

// The CEO's only look at a video-authoring task's rendered artifact before
// the post-completion render loop produces the real MP4 — awaiting_ceo_
// approval otherwise has nothing to watch. Assumes the caller already gated
// on task.source === "video"; a task that never called request_render (or
// whose frames 404 for any other reason) renders a muted empty state rather
// than nothing, since a CEO reviewing a video task with no preview at all is
// itself worth surfacing.
export function VideoPreviewCard({ task }: { task: Task }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["video", "preview-frames", task.id],
    queryFn: () => videoApi.getPreviewFrames(task.id),
    enabled: !!task.id,
    retry: false, // a 404 (nothing rendered yet) is an expected outcome, not a transient failure
  });

  const [selectedCut, setSelectedCut] = useState<VideoCut | null>(null);
  const availableCuts = (Object.keys(data?.frames ?? {}) as VideoCut[]).filter(
    (c) => (data?.frames[c]?.length ?? 0) > 0,
  );
  const cut =
    selectedCut && availableCuts.includes(selectedCut)
      ? selectedCut
      : availableCuts[0];

  return (
    <Card>
      <CardHeader>
        <HelpTip label="Preview frames from request_render — extracted before the real MP4 renders, so there's something to look at while this task awaits your approval">
          <CardTitle className="flex w-fit items-center gap-2 text-lg">
            <Clapperboard className="h-5 w-5" />
            Video preview
          </CardTitle>
        </HelpTip>
        {data && (
          <CardDescription className="flex flex-wrap items-center gap-2">
            {data.composition_id && (
              <code className="text-xs">{data.composition_id}</code>
            )}
            {data.duration_seconds != null && (
              <span>{data.duration_seconds.toFixed(1)}s clip</span>
            )}
            {data.rendered_at && (
              <span>rendered {formatAbsoluteTimestamp(data.rendered_at)}</span>
            )}
            {data.dirty && (
              <HelpTip label="The working tree had uncommitted changes when this was rendered — it may not exactly match what's pushed">
                <Badge variant="outline" className="text-amber-700">
                  uncommitted changes
                </Badge>
              </HelpTip>
            )}
          </CardDescription>
        )}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-96 w-full rounded-md" />
        ) : isError || !data || !cut ? (
          <p className="text-sm text-muted-foreground">
            No render preview yet — the developer hasn&apos;t called
            request_render on this task.
          </p>
        ) : (
          <div className="space-y-3">
            <div className="flex gap-2">
              {(Object.keys(CUT_LABELS) as VideoCut[]).map((c) => (
                <Button
                  key={c}
                  type="button"
                  size="sm"
                  variant={cut === c ? "default" : "outline"}
                  disabled={!availableCuts.includes(c)}
                  onClick={() => setSelectedCut(c)}
                >
                  {CUT_LABELS[c]}
                  {!availableCuts.includes(c) && " (missing)"}
                </Button>
              ))}
            </div>
            <FrameStepper
              key={cut}
              taskId={task.id}
              cut={cut}
              frames={data.frames[cut] ?? []}
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
