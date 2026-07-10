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

// Shared re-render control — video-post-queue.tsx (a rendered draft) and
// video-pipeline-strip.tsx (a still-in-flight authoring task) both render
// this for any composition-bearing task, regardless of its current
// render_status: the backend's rerender endpoint only requires a completed
// authoring task with a proposed composition, not a failed render, so a
// healthy render can be deliberately redone too. A confirm dialog guards the
// action since it discards whatever already rendered. Three visual states on
// the trigger button: idle (ready to click), loading (mutation in-flight),
// error (the retry itself failed — button re-enables so the CEO can try
// again). Mirrors the pipeline strip's render_failed derivation in
// video-pipeline-utils.ts.
export function RerenderControl({
  authoringTaskId,
}: {
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
