"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { roadmapApi, type RoadmapItem } from "@/lib/api/roadmap";
import { getErrorMessage } from "@/lib/api/client";
import { haptics } from "@/lib/telegram/webapp";
import { Badge } from "@/components/ui/badge";
import { TgSection } from "@/components/tg/ui";
import { PrimaryAction } from "./primary-action";
import { RejectForm } from "./reject-form";

const MIN_REJECT_CHARS = 4;
const PRIORITY_LABELS = ["critical", "high", "medium", "low"];

/** Focused roadmap item: the PO's pitch in full (description, rationale,
 * acceptance criteria), then approve into the backlog or reject. */
export function RoadmapItemDetail({
  cycleId,
  item,
  onDone,
}: {
  cycleId: string;
  item: RoadmapItem;
  onDone: () => void;
}) {
  const queryClient = useQueryClient();

  const finish = (ok: boolean, message: string) => {
    void queryClient.invalidateQueries({ queryKey: ["roadmap", "cycles"] });
    if (ok) {
      haptics.success();
      toast.success(message);
      onDone();
    } else {
      haptics.error();
      toast.warning(message);
    }
  };

  const approve = useMutation({
    mutationFn: () => roadmapApi.approveItem(cycleId, item.id),
    onSuccess: (result) =>
      finish(
        result.status === "approved" || result.status === "already_approved",
        result.status === "approved"
          ? "Approved — added to the backlog."
          : result.detail,
      ),
    onError: (err) => {
      haptics.error();
      toast.error(getErrorMessage(err));
    },
  });

  const reject = useMutation({
    mutationFn: (reason: string) =>
      roadmapApi.rejectItem(cycleId, item.id, reason),
    onSuccess: () => finish(true, "Item rejected."),
    onError: (err) => {
      haptics.error();
      toast.error(getErrorMessage(err));
    },
  });

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge variant="secondary">{item.team}</Badge>
        <Badge variant="outline">{item.project_slug}</Badge>
        <Badge>
          P{item.priority} · {PRIORITY_LABELS[item.priority] ?? "?"}
        </Badge>
      </div>

      <p className="text-sm leading-relaxed">{item.description}</p>

      <TgSection title="Why">
        <p className="text-xs text-muted-foreground">{item.rationale}</p>
      </TgSection>

      <TgSection title="Acceptance criteria">
        <ul className="list-disc space-y-0.5 pl-4 text-xs">
          {item.acceptance_criteria.map((ac, i) => (
            <li key={i}>{ac}</li>
          ))}
        </ul>
      </TgSection>

      <PrimaryAction
        text="Approve → backlog"
        loading={approve.isPending}
        onClick={() => approve.mutate()}
      />
      <RejectForm
        minChars={MIN_REJECT_CHARS}
        placeholder="Why not this one?"
        pending={reject.isPending}
        onSubmit={(reason) => reject.mutate(reason)}
      />
    </div>
  );
}
