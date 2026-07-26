"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { coronerApi } from "@/lib/api";
import type { Postmortem } from "@/lib/api/coroner";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
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
import { Skeleton } from "@/components/ui/skeleton";
import { OfflineState } from "@/components/ui/offline-state";
import { HelpTip } from "@/components/ui/help-tip";
import { CheckCircle2, Stethoscope, XCircle } from "lucide-react";
import { toast } from "sonner";

const _MIN_REASON_CHARS = 4;

const INCIDENT_KIND_LABELS: Record<string, string> = {
  bounced: "bounced 3+ times",
  cancelled: "cancelled after work started",
  budget: "budget-blocked",
};

function processChangeStatusBadge(postmortem: Postmortem) {
  if (postmortem.process_change_status === "approved") {
    return (
      <HelpTip label="Materialized as a Main-PM-owned task">
        <Badge variant="secondary" className="bg-green-600/10 text-green-700">
          Approved
        </Badge>
      </HelpTip>
    );
  }
  if (postmortem.process_change_status === "rejected") {
    return (
      <HelpTip label="Dismissed — not added to the backlog">
        <Badge variant="outline">Dismissed</Badge>
      </HelpTip>
    );
  }
  if (postmortem.process_change_status === "not_applicable") {
    return (
      <HelpTip label="Already drafted straight into the playbook review queue — nothing else to decide">
        <Badge variant="outline">Drafted as playbook</Badge>
      </HelpTip>
    );
  }
  return null;
}

function PostmortemRow({
  postmortem,
  onApprove,
  onReject,
  approving,
}: {
  postmortem: Postmortem;
  onApprove: (taskId: string) => void;
  onReject: (postmortem: Postmortem) => void;
  approving: boolean;
}) {
  const isProposed = postmortem.process_change_status === "proposed";

  return (
    <div className="rounded-lg border p-4 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">
          {postmortem.incident_title ?? "Untitled incident"}
        </span>
        {postmortem.incident_kind && (
          <HelpTip label="What triggered this autopsy">
            <Badge variant="outline">
              {INCIDENT_KIND_LABELS[postmortem.incident_kind] ??
                postmortem.incident_kind}
            </Badge>
          </HelpTip>
        )}
        {postmortem.failed_stage && (
          <HelpTip label="The lifecycle stage where it actually broke down">
            <Badge variant="secondary">{postmortem.failed_stage}</Badge>
          </HelpTip>
        )}
        {postmortem.process_change_kind && (
          <HelpTip label="The kind of process change proposed">
            <Badge>{postmortem.process_change_kind}</Badge>
          </HelpTip>
        )}
        {processChangeStatusBadge(postmortem)}
      </div>
      {postmortem.incident_summary && (
        <p className="text-sm text-muted-foreground">
          {postmortem.incident_summary}
        </p>
      )}
      {postmortem.root_cause && (
        <p className="text-sm">
          <span className="font-semibold">Root cause: </span>
          {postmortem.root_cause}
        </p>
      )}
      {postmortem.process_change_description && (
        <p className="text-sm">
          <span className="font-semibold">Process change: </span>
          {postmortem.process_change_description}
        </p>
      )}
      {postmortem.process_change_status === "rejected" &&
        postmortem.process_change_reject_reason && (
          <p className="text-sm text-destructive">
            Dismissed: {postmortem.process_change_reject_reason}
          </p>
        )}
      {postmortem.completed_at && (
        <p className="text-xs text-muted-foreground">
          {new Date(postmortem.completed_at).toLocaleString()}
        </p>
      )}
      {isProposed && (
        <div className="flex justify-end gap-2 pt-1">
          <HelpTip label="Records your reason — not added to the backlog">
            <Button
              variant="outline"
              size="sm"
              className="text-destructive hover:text-destructive"
              onClick={() => onReject(postmortem)}
            >
              <XCircle className="mr-1 h-3.5 w-3.5" />
              Dismiss
            </Button>
          </HelpTip>
          <HelpTip label="Materializes this process change as a Main-PM-owned task — needs normal PM activation to start">
            <Button
              size="sm"
              className="bg-green-600 hover:bg-green-700"
              disabled={approving}
              onClick={() => onApprove(postmortem.task_id)}
            >
              <CheckCircle2 className="mr-1 h-3.5 w-3.5" />
              Approve
            </Button>
          </HelpTip>
        </div>
      )}
    </div>
  );
}

// Postmortems list — a Coroner postmortem completes atomically at
// propose_postmortem time (no per-item approve/reject at the TASK level
// like Pest Control/Roadmap), so this never hides on empty; it's a
// retrospective report the CEO checks. Its single process change still
// carries its own per-item approve/dismiss action, below each row.
export function CoronerPostmortemsCard({ className }: { className?: string }) {
  const queryClient = useQueryClient();
  const [rejecting, setRejecting] = useState<Postmortem | null>(null);
  const [reason, setReason] = useState("");
  const [approvingTaskId, setApprovingTaskId] = useState<string | null>(null);

  const {
    data: postmortems,
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: ["coroner", "postmortems"],
    queryFn: () => coronerApi.listPostmortems(),
    refetchInterval: 30000,
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["coroner", "postmortems"] });

  const approveMutation = useMutation({
    mutationFn: (taskId: string) => coronerApi.approveProcessChange(taskId),
    onSuccess: (result) => {
      invalidate();
      if (
        result.status === "approved" ||
        result.status === "already_approved"
      ) {
        toast.success("Process change approved — materialized as a task");
      } else {
        toast.warning(result.detail);
      }
    },
    onError: (e) =>
      toast.error(
        `Approve failed: ${e instanceof Error ? e.message : "error"}`,
      ),
    onSettled: () => setApprovingTaskId(null),
  });

  const rejectMutation = useMutation({
    mutationFn: ({ taskId, reason }: { taskId: string; reason: string }) =>
      coronerApi.rejectProcessChange(taskId, reason),
    onSuccess: () => {
      invalidate();
      toast.success("Process change dismissed");
      closeReject();
    },
    onError: (e) =>
      toast.error(
        `Dismiss failed: ${e instanceof Error ? e.message : "error"}`,
      ),
  });

  const closeReject = () => {
    setRejecting(null);
    setReason("");
  };

  const confirmReject = () => {
    if (!rejecting) return;
    if (reason.trim().length < _MIN_REASON_CHARS) {
      toast.error("Give a brief reason for dismissing");
      return;
    }
    rejectMutation.mutate({ taskId: rejecting.task_id, reason: reason.trim() });
  };

  const handleApprove = (taskId: string) => {
    setApprovingTaskId(taskId);
    approveMutation.mutate(taskId);
  };

  return (
    <>
      <Card className={className}>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Stethoscope className="h-5 w-5" />
            Postmortems
          </CardTitle>
          <CardDescription>
            The Auditor&apos;s Coroner autopsies — bounced, cancelled, or
            budget-blocked incidents, each with a root cause and one proposed
            process change.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-3">
              <Skeleton className="h-20 w-full" />
              <Skeleton className="h-20 w-full" />
            </div>
          ) : isError ? (
            <OfflineState
              title="Failed to load Postmortems"
              description="Could not reach the orchestrator API. Check the backend is running."
              onRetry={() => void refetch()}
            />
          ) : !postmortems || postmortems.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No postmortems yet — Coroner autopsies open only when a task
              bounces 3+ times, is cancelled after work started, or is
              budget-blocked.
            </p>
          ) : (
            <div className="space-y-3">
              {postmortems.map((pm) => (
                <PostmortemRow
                  key={pm.task_id}
                  postmortem={pm}
                  onApprove={handleApprove}
                  onReject={setRejecting}
                  approving={approvingTaskId === pm.task_id}
                />
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Dialog open={!!rejecting} onOpenChange={() => closeReject()}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Dismiss process change</DialogTitle>
            <DialogDescription>
              This records your reason and feeds the next cycle&apos;s prompt —
              it is not added to the backlog.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="coroner-reject-reason">Reason</Label>
            <Textarea
              id="coroner-reject-reason"
              placeholder="e.g. one-off incident; not worth a standing process change..."
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={closeReject}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={confirmReject}
              disabled={rejectMutation.isPending}
            >
              {rejectMutation.isPending ? "Dismissing..." : "Dismiss"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
