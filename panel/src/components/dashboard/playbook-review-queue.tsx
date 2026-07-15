"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { playbooksApi } from "@/lib/api";
import type { Playbook } from "@/lib/api/playbooks";
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
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { BookOpen, CheckCircle2, XCircle } from "lucide-react";
import { toast } from "sonner";
import { HelpTip } from "@/components/ui/help-tip";

const _MIN_REASON = 4;

// Auditor/CEO review queue for drafted playbooks. Hidden when none are pending
// (mirrors the PR-review + release-proposal cards).
export function PlaybookReviewQueue({ className }: { className?: string }) {
  const queryClient = useQueryClient();
  const [rejecting, setRejecting] = useState<Playbook | null>(null);
  const [reason, setReason] = useState("");

  const { data: drafts, isLoading } = useQuery({
    queryKey: ["playbooks", "drafts"],
    queryFn: () => playbooksApi.listDrafts(),
    refetchInterval: 30000,
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["playbooks", "drafts"] });

  const approveMutation = useMutation({
    mutationFn: (id: string) => playbooksApi.approve(id),
    onSuccess: () => {
      invalidate();
      toast.success("Playbook approved and indexed");
    },
    onError: (e) =>
      toast.error(
        `Approve failed: ${e instanceof Error ? e.message : "error"}`,
      ),
  });

  const rejectMutation = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      playbooksApi.reject(id, reason),
    onSuccess: () => {
      invalidate();
      toast.success("Playbook rejected (archived)");
      closeReject();
    },
    onError: (e) =>
      toast.error(`Reject failed: ${e instanceof Error ? e.message : "error"}`),
  });

  const closeReject = () => {
    setRejecting(null);
    setReason("");
  };

  const confirmReject = () => {
    if (!rejecting) return;
    if (reason.trim().length < _MIN_REASON) {
      toast.error("Give a brief reason for rejecting");
      return;
    }
    rejectMutation.mutate({ id: rejecting.id, reason: reason.trim() });
  };

  if (isLoading || !drafts || drafts.length === 0) return null;

  return (
    <>
      <Card className={className}>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <HelpTip label="Procedures agents drafted from real task runs, awaiting your curation call">
              <span className="inline-flex items-center gap-2">
                <BookOpen className="h-5 w-5" />
                Playbook Review
              </span>
            </HelpTip>
            <HelpTip label="Drafts awaiting a decision">
              <Badge variant="secondary">{drafts.length}</Badge>
            </HelpTip>
          </CardTitle>
          <CardDescription>
            Drafted playbooks awaiting your approval — approved ones are indexed
            and auto-suggested to agents.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {drafts.map((pb) => (
            <div
              key={pb.id}
              className="rounded-lg border p-4 transition-colors hover:bg-muted/50"
            >
              <div className="mb-1 flex items-center gap-2">
                <span className="font-medium">{pb.title}</span>
                {pb.team && (
                  <HelpTip label="Team that authored this playbook">
                    <Badge variant="outline">{pb.team}</Badge>
                  </HelpTip>
                )}
                {pb.tags.map((t) => (
                  <HelpTip key={t} label="Search/filter tag for this playbook">
                    <Badge variant="secondary" className="text-xs">
                      {t}
                    </Badge>
                  </HelpTip>
                ))}
              </div>
              <p className="text-sm text-muted-foreground">
                <span className="font-semibold">When:</span> {pb.problem}
              </p>
              <pre className="mt-2 max-h-40 overflow-auto rounded bg-muted p-2 text-xs whitespace-pre-wrap">
                {pb.procedure}
              </pre>
              <div className="mt-3 flex flex-col-reverse gap-2 sm:flex-row sm:items-center sm:justify-end">
                <HelpTip label="Archives this draft — it is never indexed">
                  <Button
                    variant="outline"
                    size="sm"
                    className="text-destructive hover:text-destructive"
                    onClick={() => setRejecting(pb)}
                  >
                    <XCircle className="mr-1 h-4 w-4" />
                    Reject
                  </Button>
                </HelpTip>
                <HelpTip label="Indexes this playbook so it's auto-suggested to agents">
                  <Button
                    size="sm"
                    className="bg-green-600 hover:bg-green-700"
                    disabled={
                      approveMutation.isPending &&
                      approveMutation.variables === pb.id
                    }
                    onClick={() => approveMutation.mutate(pb.id)}
                  >
                    <CheckCircle2 className="mr-1 h-4 w-4" />
                    Approve
                  </Button>
                </HelpTip>
              </div>
            </div>
          ))}
        </CardContent>
      </Card>

      <Dialog open={!!rejecting} onOpenChange={() => closeReject()}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reject playbook</DialogTitle>
            <DialogDescription>
              This archives the playbook. Give a brief reason (it is recorded).
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="reject-reason">Reason</Label>
            <Textarea
              id="reject-reason"
              placeholder="e.g. duplicate of an existing playbook; too task-specific..."
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
              {rejectMutation.isPending ? "Rejecting..." : "Reject & Archive"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
