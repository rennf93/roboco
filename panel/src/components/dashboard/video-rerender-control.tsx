"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { videoApi } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { RefreshCw } from "lucide-react";
import { toast } from "sonner";

/**
 * Shared re-render control — renders a button + confirm dialog for re-triggering
 * video render operations. Used by both video-post-queue.tsx (a rendered draft)
 * and video-pipeline-strip.tsx (a still-in-flight authoring task).
 *
 * @component
 *
 * Gating Logic:
 * - Shows for any task with source_task_id + composition_id, regardless of
 *   render_status. The backend's rerender endpoint only requires a completed
 *   authoring task with a proposed composition, not a failed render, so a
 *   healthy render can be deliberately redone too.
 *
 * Behavior:
 * - Trigger button opens a Dialog with Cancel and Re-render buttons (guards
 *   against accidental clicks, since re-rendering discards whatever already
 *   rendered).
 * - Only the confirm click calls videoApi.rerender(authoringTaskId).
 * - Three visual states on the trigger button:
 *   1. Idle: "Re-render" (ready to click)
 *   2. Loading: "Re-rendering..." with spinning icon (mutation in-flight)
 *   3. Error: "Retry re-render" in red (the retry itself failed; button stays
 *      enabled so the CEO can try again).
 *
 * @example
 * ```tsx
 * // Usage in video-post-queue for a rendered draft:
 * {canRerender && (
 *   <div className="ml-auto">
 *     <RerenderControl authoringTaskId={post.source_task_id as string} />
 *   </div>
 * )}
 * ```
 *
 * @example
 * ```tsx
 * // Usage in video-pipeline-strip for an in-flight authoring task:
 * {canRerender && (
 *   <div className="ml-auto">
 *     <RerenderControl authoringTaskId={item.task_id} />
 *   </div>
 * )}
 * ```
 */
export function RerenderControl({
  authoringTaskId,
}: {
  /**
   * The authoring task ID to re-render. Passed to videoApi.rerender(authoringTaskId).
   * For video-post-queue: the post's source_task_id.
   * For video-pipeline-strip: the pipeline item's own task_id (a pipeline item IS
   * the authoring task).
   */
  authoringTaskId: string;
}) {
  const queryClient = useQueryClient();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const rerenderMutation = useMutation({
    mutationFn: () => videoApi.rerender(authoringTaskId),
    onSuccess: () => {
      toast.success("Re-render queued — it will re-pick up on the next cycle.");
      queryClient.invalidateQueries({ queryKey: ["video", "pipeline"] });
      queryClient.invalidateQueries({ queryKey: ["video", "posts"] });
    },
    onError: (e) =>
      toast.error(
        `Re-render failed: ${e instanceof Error ? e.message : "error"}`,
      ),
  });

  return (
    <>
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={rerenderMutation.isPending}
        onClick={() => setConfirmOpen(true)}
        className={
          rerenderMutation.isError
            ? "border-destructive text-destructive"
            : undefined
        }
      >
        <RefreshCw
          className={`mr-1 h-4 w-4 ${rerenderMutation.isPending ? "animate-spin" : ""}`}
        />
        {rerenderMutation.isPending
          ? "Re-rendering..."
          : rerenderMutation.isError
            ? "Retry re-render"
            : "Re-render"}
      </Button>
      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Re-render this video?</DialogTitle>
            <DialogDescription>
              This clears the current render and queues a fresh one from the
              same composition. Any already-rendered cuts stay visible until
              the new render finishes.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={() => {
                setConfirmOpen(false);
                rerenderMutation.mutate();
              }}
            >
              Re-render
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
