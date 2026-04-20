"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { tasksApi } from "@/lib/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
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
import { CheckCircle2, XCircle, Clock, FileText, ExternalLink } from "lucide-react";
import Link from "next/link";
import type { Task } from "@/types";
import { toast } from "sonner";

interface CeoApprovalQueueProps {
  className?: string;
}

export function CeoApprovalQueue({ className }: CeoApprovalQueueProps) {
  const queryClient = useQueryClient();
  const [selectedTask, setSelectedTask] = useState<Task | null>(null);
  const [actionType, setActionType] = useState<"approve" | "reject" | null>(null);
  const [notes, setNotes] = useState("");

  // Fetch tasks awaiting CEO approval
  const { data: tasks, isLoading } = useQuery({
    queryKey: ["tasks", "awaiting-ceo-approval"],
    queryFn: () => tasksApi.getAwaitingCeoApproval(),
    refetchInterval: 30000, // Refresh every 30 seconds
  });

  // Approve mutation
  const approveMutation = useMutation({
    mutationFn: ({ taskId, notes }: { taskId: string; notes?: string }) =>
      tasksApi.ceoApprove(taskId, notes),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      toast.success("Task approved and completed");
      closeDialog();
    },
    onError: (error) => {
      toast.error(`Failed to approve: ${error instanceof Error ? error.message : "Unknown error"}`);
    },
  });

  // Reject mutation
  const rejectMutation = useMutation({
    mutationFn: ({ taskId, notes }: { taskId: string; notes: string }) =>
      tasksApi.ceoReject(taskId, notes),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      toast.success("Task rejected and sent back for revision");
      closeDialog();
    },
    onError: (error) => {
      toast.error(`Failed to reject: ${error instanceof Error ? error.message : "Unknown error"}`);
    },
  });

  const openDialog = (task: Task, action: "approve" | "reject") => {
    setSelectedTask(task);
    setActionType(action);
    setNotes("");
  };

  const closeDialog = () => {
    setSelectedTask(null);
    setActionType(null);
    setNotes("");
  };

  const handleConfirm = () => {
    if (!selectedTask) return;

    if (actionType === "approve") {
      approveMutation.mutate({ taskId: selectedTask.id, notes: notes || undefined });
    } else if (actionType === "reject") {
      if (!notes.trim()) {
        toast.error("Rejection reason is required");
        return;
      }
      rejectMutation.mutate({ taskId: selectedTask.id, notes });
    }
  };

  const getPriorityBadge = (priority: number) => {
    const variants: Record<number, { label: string; variant: "default" | "secondary" | "destructive" | "outline" }> = {
      0: { label: "P0", variant: "destructive" },
      1: { label: "P1", variant: "destructive" },
      2: { label: "P2", variant: "secondary" },
      3: { label: "P3", variant: "outline" },
    };
    const { label, variant } = variants[priority] || { label: `P${priority}`, variant: "outline" as const };
    return <Badge variant={variant}>{label}</Badge>;
  };

  if (isLoading) {
    return (
      <Card className={className}>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Clock className="h-5 w-5" />
            CEO Approval Queue
          </CardTitle>
          <CardDescription>Tasks awaiting your approval</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            {[1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-20 w-full" />
            ))}
          </div>
        </CardContent>
      </Card>
    );
  }

  const pendingTasks = tasks || [];

  return (
    <>
      <Card className={className}>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Clock className="h-5 w-5" />
            CEO Approval Queue
            {pendingTasks.length > 0 && (
              <Badge variant="secondary" className="ml-2">
                {pendingTasks.length}
              </Badge>
            )}
          </CardTitle>
          <CardDescription>Tasks escalated for your final approval</CardDescription>
        </CardHeader>
        <CardContent>
          {pendingTasks.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              <CheckCircle2 className="h-12 w-12 mx-auto mb-2 opacity-50" />
              <p>No tasks awaiting approval</p>
            </div>
          ) : (
            <div className="space-y-3">
              {pendingTasks.map((task) => (
                <div
                  key={task.id}
                  className="flex items-start justify-between p-4 border rounded-lg hover:bg-muted/50 transition-colors"
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      {getPriorityBadge(task.priority)}
                      <Badge variant="outline">{task.team}</Badge>
                    </div>
                    <Link
                      href={`/tasks/${task.id}`}
                      className="font-medium hover:underline line-clamp-1"
                    >
                      {task.title}
                    </Link>
                    {task.quick_context && (
                      <p className="text-sm text-muted-foreground mt-1 line-clamp-2">
                        {task.quick_context}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-2 ml-4 flex-shrink-0">
                    <Link href={`/tasks/${task.id}`}>
                      <Button variant="ghost" size="sm">
                        <FileText className="h-4 w-4" />
                      </Button>
                    </Link>
                    <Button
                      variant="outline"
                      size="sm"
                      className="text-destructive hover:text-destructive"
                      onClick={() => openDialog(task, "reject")}
                    >
                      <XCircle className="h-4 w-4 mr-1" />
                      Reject
                    </Button>
                    <Button
                      size="sm"
                      className="bg-green-600 hover:bg-green-700"
                      onClick={() => openDialog(task, "approve")}
                    >
                      <CheckCircle2 className="h-4 w-4 mr-1" />
                      Approve
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Confirmation Dialog */}
      <Dialog open={!!selectedTask && !!actionType} onOpenChange={() => closeDialog()}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {actionType === "approve" ? "Approve Task" : "Reject Task"}
            </DialogTitle>
            <DialogDescription>
              {actionType === "approve"
                ? "This will complete the task and notify the team."
                : "This will send the task back for revision."}
            </DialogDescription>
          </DialogHeader>

          {selectedTask && (
            <div className="py-4">
              <div className="flex items-center gap-2 mb-2">
                {getPriorityBadge(selectedTask.priority)}
                <Badge variant="outline">{selectedTask.team}</Badge>
              </div>
              <p className="font-medium">{selectedTask.title}</p>
              {selectedTask.description && (
                <p className="text-sm text-muted-foreground mt-2 line-clamp-3">
                  {selectedTask.description}
                </p>
              )}
              <Link
                href={`/tasks/${selectedTask.id}`}
                target="_blank"
                className="text-sm text-primary flex items-center gap-1 mt-2 hover:underline"
              >
                View full details <ExternalLink className="h-3 w-3" />
              </Link>
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="notes">
              {actionType === "approve" ? "Notes (optional)" : "Reason for rejection (required)"}
            </Label>
            <Textarea
              id="notes"
              placeholder={
                actionType === "approve"
                  ? "Add any notes about this approval..."
                  : "Explain what needs to be fixed..."
              }
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
              disabled={approveMutation.isPending || rejectMutation.isPending}
              className={actionType === "approve" ? "bg-green-600 hover:bg-green-700" : ""}
              variant={actionType === "reject" ? "destructive" : "default"}
            >
              {approveMutation.isPending || rejectMutation.isPending
                ? "Processing..."
                : actionType === "approve"
                  ? "Approve & Complete"
                  : "Reject & Request Revision"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
