"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { tasksApi } from "@/lib/api";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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
import { GitPullRequest, ExternalLink, Rocket, XCircle, FileText } from "lucide-react";
import Link from "next/link";
import { type Task } from "@/types";
import { toast } from "sonner";

interface PrReviewQueueProps {
  className?: string;
}

/**
 * The CEO's decision queue for inbound external PRs the org has reviewed.
 *
 * The PR reviewer is read-only — it posts one change-request and stops; the CEO
 * decides what happens next. This surfaces each reviewed PR with two actions:
 * Supersede (the org takes the contribution over and finishes it) or Dismiss
 * (drop it from the queue; the review stays on the GitHub PR). Hidden when empty.
 */
export function PrReviewQueue({ className }: PrReviewQueueProps) {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<Task | null>(null);
  const [action, setAction] = useState<"supersede" | "dismiss" | null>(null);

  const { data: reviews, isLoading } = useQuery({
    queryKey: ["tasks", "external-pr-reviews"],
    queryFn: () => tasksApi.getExternalPrReviews(),
    refetchInterval: 30000,
  });

  const supersedeMutation = useMutation({
    mutationFn: (taskId: string) => tasksApi.supersedeExternalPr(taskId),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      toast.success(
        res.ok
          ? "Superseding — the org is taking the PR over"
          : "Supersede did not start",
      );
      close();
    },
    onError: (e) =>
      toast.error(
        `Supersede failed: ${e instanceof Error ? e.message : "Unknown error"}`,
      ),
  });

  const dismissMutation = useMutation({
    mutationFn: (taskId: string) => tasksApi.dismissExternalPr(taskId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      toast.success("Review dismissed");
      close();
    },
    onError: (e) =>
      toast.error(
        `Dismiss failed: ${e instanceof Error ? e.message : "Unknown error"}`,
      ),
  });

  const open = (task: Task, a: "supersede" | "dismiss") => {
    setSelected(task);
    setAction(a);
  };
  const close = () => {
    setSelected(null);
    setAction(null);
  };
  const confirm = () => {
    if (!selected) return;
    if (action === "supersede") supersedeMutation.mutate(selected.id);
    else if (action === "dismiss") dismissMutation.mutate(selected.id);
  };

  if (isLoading) {
    return (
      <Card className={className}>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <GitPullRequest className="h-5 w-5" />
            PR Reviews
          </CardTitle>
          <CardDescription>
            External PRs reviewed and awaiting your call
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            {[1, 2].map((i) => (
              <Skeleton key={i} className="h-20 w-full" />
            ))}
          </div>
        </CardContent>
      </Card>
    );
  }

  const items = reviews || [];
  if (items.length === 0) return null; // keep the dashboard clean when there's nothing to decide

  const isPending = supersedeMutation.isPending || dismissMutation.isPending;

  return (
    <>
      <Card className={className}>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <GitPullRequest className="h-5 w-5" />
            PR Reviews
            <Badge variant="secondary" className="ml-2">
              {items.length}
            </Badge>
          </CardTitle>
          <CardDescription>
            External PRs the org reviewed — supersede or dismiss
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            {items.map((task) => (
              <div
                key={task.id}
                className="flex items-start justify-between p-4 border rounded-lg hover:bg-muted/50 transition-colors"
              >
                <div className="flex-1 min-w-0">
                  <Link
                    href={`/tasks/${task.id}`}
                    className="font-medium hover:underline line-clamp-1"
                  >
                    {task.title}
                  </Link>
                  {task.description && (
                    <p className="text-sm text-muted-foreground mt-1 line-clamp-2">
                      {task.description}
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-2 ml-4 flex-shrink-0">
                  {task.pr_url && (
                    <a
                      href={task.pr_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      title="View PR on GitHub"
                    >
                      <Button variant="ghost" size="sm">
                        <ExternalLink className="h-4 w-4" />
                      </Button>
                    </a>
                  )}
                  <Link href={`/tasks/${task.id}`} title="Review details">
                    <Button variant="ghost" size="sm">
                      <FileText className="h-4 w-4" />
                    </Button>
                  </Link>
                  <Button
                    variant="outline"
                    size="sm"
                    className="text-destructive hover:text-destructive"
                    onClick={() => open(task, "dismiss")}
                  >
                    <XCircle className="h-4 w-4 mr-1" />
                    Dismiss
                  </Button>
                  <Button
                    size="sm"
                    className="bg-blue-600 hover:bg-blue-700"
                    onClick={() => open(task, "supersede")}
                  >
                    <Rocket className="h-4 w-4 mr-1" />
                    Supersede
                  </Button>
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      <Dialog open={!!selected && !!action} onOpenChange={() => close()}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {action === "supersede" ? "Supersede this PR" : "Dismiss this review"}
            </DialogTitle>
            <DialogDescription>
              {action === "supersede"
                ? "The org takes the contribution over: a roboco-owned branch is cut from the contributor's commits, a cell finishes and hardens it, then opens our own PR. This authorizes fetching and running the contributor's code."
                : "Removes this PR from your review queue. The review stays on the GitHub PR; the org takes no further action."}
            </DialogDescription>
          </DialogHeader>
          {selected && (
            <div className="py-2">
              <p className="font-medium">{selected.title}</p>
              {selected.pr_url && (
                <a
                  href={selected.pr_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-sm text-primary flex items-center gap-1 mt-2 hover:underline"
                >
                  View PR on GitHub <ExternalLink className="h-3 w-3" />
                </a>
              )}
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={close}>
              Cancel
            </Button>
            <Button
              onClick={confirm}
              disabled={isPending}
              variant={action === "dismiss" ? "destructive" : "default"}
              className={action === "supersede" ? "bg-blue-600 hover:bg-blue-700" : ""}
            >
              {isPending
                ? "Processing..."
                : action === "supersede"
                  ? "Supersede"
                  : "Dismiss"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
