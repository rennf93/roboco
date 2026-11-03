"use client";

import { useState } from "react";
import axios from "axios";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { playbooksApi } from "@/lib/api";
import type { Playbook } from "@/lib/api/playbooks";
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
import { Archive } from "lucide-react";
import { toast } from "sonner";

const _CONFLICT = 409;

// Approved playbooks with a confirm-gated archive (retires a stale or wrong
// one and de-indexes it from the KB).
export function PlaybookApprovedList() {
  const queryClient = useQueryClient();
  const [archiving, setArchiving] = useState<Playbook | null>(null);

  const {
    data: approved,
    isLoading,
    isError,
  } = useQuery({
    queryKey: ["playbooks", "approved"],
    queryFn: () => playbooksApi.listApproved(),
    refetchInterval: 30000,
  });

  const invalidateBoth = () => {
    queryClient.invalidateQueries({ queryKey: ["playbooks", "drafts"] });
    queryClient.invalidateQueries({ queryKey: ["playbooks", "approved"] });
  };

  const archiveMutation = useMutation({
    mutationFn: (id: string) => playbooksApi.archive(id),
    onSuccess: () => {
      invalidateBoth();
      toast.success("Playbook archived and removed from the index");
      setArchiving(null);
    },
    onError: (e) => {
      if (axios.isAxiosError(e) && e.response?.status === _CONFLICT) {
        invalidateBoth();
        toast.info("That playbook was already archived");
      } else {
        toast.error(
          `Archive failed: ${e instanceof Error ? e.message : "error"}`,
        );
      }
      setArchiving(null);
    },
  });

  if (isLoading) {
    return (
      <div className="space-y-3" data-testid="approved-loading">
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-20 w-full" />
      </div>
    );
  }
  if (isError || !approved) {
    return (
      <p role="alert" className="text-sm text-destructive">
        Could not load approved playbooks.
      </p>
    );
  }
  if (approved.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No approved playbooks yet.
      </p>
    );
  }

  return (
    <>
      {approved.map((pb) => (
        <div key={pb.id} className="rounded-lg border p-4">
          <div className="mb-1 flex flex-wrap items-center gap-2">
            <span className="font-medium">{pb.title}</span>
            {pb.team && <Badge variant="outline">{pb.team}</Badge>}
            {pb.tags.map((t) => (
              <Badge key={t} variant="secondary" className="text-xs">
                {t}
              </Badge>
            ))}
          </div>
          <p className="text-sm text-muted-foreground">
            <span className="font-semibold">When:</span> {pb.problem}
          </p>
          <div className="mt-3 flex justify-end">
            <Button
              variant="outline"
              size="sm"
              className="text-destructive hover:text-destructive"
              onClick={() => setArchiving(pb)}
            >
              <Archive className="mr-1 h-4 w-4" />
              Archive
            </Button>
          </div>
        </div>
      ))}

      <Dialog open={!!archiving} onOpenChange={() => setArchiving(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Archive playbook</DialogTitle>
            <DialogDescription>
              Archive &quot;{archiving?.title}&quot;? It is removed from the
              knowledge base and no longer suggested to agents.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setArchiving(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={archiveMutation.isPending}
              onClick={() => archiving && archiveMutation.mutate(archiving.id)}
            >
              {archiveMutation.isPending ? "Archiving..." : "Confirm archive"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
