"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { tasksApi } from "@/lib/api";
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

interface ApproveAndStartButtonProps {
  task: Task;
}

export function ApproveAndStartButton({ task }: ApproveAndStartButtonProps) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [notes, setNotes] = useState("");

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

      <Dialog open={open} onOpenChange={(next) => (next ? setOpen(true) : closeDialog())}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Approve &amp; Start</DialogTitle>
            <DialogDescription>
              Hand this board-reviewed task to Main PM so work can begin. This
              note is the permanent audit record and is required.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-2">
            <Label htmlFor="approve-and-start-notes">Approval notes (required)</Label>
            <Textarea
              id="approve-and-start-notes"
              placeholder="Board review complete; requirements are clear. Build it..."
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
              {approveAndStartMutation.isPending ? "Starting..." : "Approve & Start"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
