"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { tasksApi } from "@/lib/api";
import { useBoardReview } from "@/hooks/use-tasks";
import { Markdown } from "@/components/ui/markdown";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Rocket } from "lucide-react";
import type { Task } from "@/types";
import { toast } from "sonner";
import { HelpTip } from "@/components/ui/help-tip";

interface ApproveAndStartButtonProps {
  task: Task;
}

function roleLabel(role: string): string {
  if (role === "product_owner") return "Product Owner";
  if (role === "head_marketing") return "Head of Marketing";
  return role;
}

export function ApproveAndStartButton({ task }: ApproveAndStartButtonProps) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [notes, setNotes] = useState("");
  // Fetch the board's actual review only while the dialog is open, so the CEO
  // reads the PO + Head of Marketing analysis before approving.
  const { data: boardReview = [], isLoading: boardLoading } = useBoardReview(
    task.id,
    open,
  );

  const approveAndStartMutation = useMutation({
    mutationFn: ({ taskId, notes }: { taskId: string; notes: string }) =>
      tasksApi.approveAndStart(taskId, notes),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      toast.success("Task approved and handed to Main PM");
      closeDialog();
    },
    onError: (error) => {
      toast.error(
        `Failed to approve & start: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    },
  });

  const closeDialog = () => {
    setOpen(false);
    setNotes("");
  };

  const handleConfirm = () => {
    // The approval note is the audit record for starting the work —
    // required and substantive (>= 20 chars), matching the server gate.
    if (notes.trim().length < 20) {
      toast.error("Approval notes are required (>= 20 characters)");
      return;
    }
    approveAndStartMutation.mutate({ taskId: task.id, notes: notes.trim() });
  };

  return (
    <>
      <Button
        size="sm"
        className="bg-green-600 hover:bg-green-700"
        onClick={() => setOpen(true)}
      >
        <Rocket className="h-4 w-4 mr-1" />
        Approve &amp; Start
      </Button>

      <Dialog
        open={open}
        onOpenChange={(next) => (next ? setOpen(true) : closeDialog())}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Approve &amp; Start</DialogTitle>
            <DialogDescription>
              Hand this board-reviewed task to Main PM so work can begin. This
              note is the permanent audit record and is required.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-2">
            <Label>Board review (Product Owner &amp; Head of Marketing)</Label>
            {boardLoading ? (
              <p className="text-xs text-muted-foreground">
                Loading board review…
              </p>
            ) : boardReview.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                No board review recorded for this task yet.
              </p>
            ) : (
              <div className="max-h-72 space-y-3 overflow-y-auto rounded-md border p-3">
                {boardReview.map((entry) => (
                  <div
                    key={entry.timestamp ?? `${entry.author}-${entry.title}`}
                    className="space-y-1"
                  >
                    <span className="inline-block rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
                      {roleLabel(entry.author_role)}
                    </span>
                    <div className="text-sm font-medium">{entry.title}</div>
                    <Markdown compact>{entry.content}</Markdown>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="space-y-2">
            <HelpTip label="Minimum 20 characters — this is the permanent audit record for starting the work.">
              <Label htmlFor="approve-and-start-notes">
                Approval notes (required)
              </Label>
            </HelpTip>
            <Textarea
              id="approve-and-start-notes"
              placeholder="Board review read; requirements are clear. Build it..."
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={3}
            />
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={closeDialog}>
              Cancel
            </Button>
            <Button
              onClick={handleConfirm}
              disabled={approveAndStartMutation.isPending}
              className="bg-green-600 hover:bg-green-700"
            >
              {approveAndStartMutation.isPending
                ? "Starting..."
                : "Approve & Start"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
