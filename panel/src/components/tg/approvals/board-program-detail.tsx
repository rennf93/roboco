"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { pestControlApi, type PestHuntItem } from "@/lib/api/pest-control";
import { spackleApi, type GapFillItem } from "@/lib/api/spackle";
import { scalesApi, type RebalanceItem } from "@/lib/api/scales";
import { dogfoodApi, type FrictionFixItem } from "@/lib/api/dogfood";
import { getErrorMessage } from "@/lib/api/client";
import { haptics } from "@/lib/telegram/webapp";
import { Badge } from "@/components/ui/badge";
import { TgSection } from "@/components/tg/ui";
import { PrimaryAction } from "./primary-action";
import { RejectForm } from "./reject-form";

const MIN_REJECT_CHARS = 4;
const PRIORITY_LABELS = ["critical", "high", "medium", "low"];

/** The per-source approve/reject endpoints the desktop queues drive, keyed
 * the same way so phone and desktop ride identical calls. */
const SOURCES = {
  pest_control: {
    approve: (cycleId: string, itemId: string) =>
      pestControlApi.approveItem(cycleId, itemId),
    reject: (cycleId: string, itemId: string, reason: string) =>
      pestControlApi.rejectItem(cycleId, itemId, reason),
    // The shared query key the desktop queue invalidates on action.
    queryKey: ["pest-control", "cycles"] as const,
    successText: "Approved — added to the backlog.",
  },
  spackle: {
    approve: (cycleId: string, itemId: string) =>
      spackleApi.approveItem(cycleId, itemId),
    reject: (cycleId: string, itemId: string, reason: string) =>
      spackleApi.rejectItem(cycleId, itemId, reason),
    queryKey: ["spackle", "cycles"] as const,
    successText: "Approved — added to the backlog.",
  },
  dogfood: {
    approve: (cycleId: string, itemId: string) =>
      dogfoodApi.approveItem(cycleId, itemId),
    reject: (cycleId: string, itemId: string, reason: string) =>
      dogfoodApi.rejectItem(cycleId, itemId, reason),
    queryKey: ["dogfood", "cycles"] as const,
    successText: "Approved — added to the backlog.",
  },
} as const;

/** Focused Pest Control / Spackle / Dogfood item: the PO's evidence-backed
 * draft in full (description, evidence, acceptance criteria), then approve
 * into the backlog or reject. All three queues share the item shape and the
 * per-item endpoint pattern, so one detail drives three sources. */
export function BoardProgramItemDetail({
  kind,
  cycleId,
  item,
  onDone,
}: {
  kind: keyof typeof SOURCES;
  cycleId: string;
  item: PestHuntItem | GapFillItem | FrictionFixItem;
  onDone: () => void;
}) {
  const queryClient = useQueryClient();
  const source = SOURCES[kind];

  const finish = (ok: boolean, message: string) => {
    void queryClient.invalidateQueries({ queryKey: source.queryKey });
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
    mutationFn: () => source.approve(cycleId, item.id),
    onSuccess: (result: { status: string; detail: string }) =>
      finish(
        result.status === "approved" || result.status === "already_approved",
        result.status === "approved" ? source.successText : result.detail,
      ),
    onError: (err) => {
      haptics.error();
      toast.error(getErrorMessage(err));
    },
  });

  const reject = useMutation({
    mutationFn: (reason: string) => source.reject(cycleId, item.id, reason),
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

      <TgSection title="Evidence">
        <p className="text-xs text-muted-foreground">{item.evidence}</p>
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

/** Focused Scales rebalance item: the target plus rationale, then apply the
 * change to the live task or reject it. Approve here EXECUTES — nothing is
 * materialized, the target task is mutated in place. */
export function ScalesItemDetail({
  cycleId,
  item,
  onDone,
}: {
  cycleId: string;
  item: RebalanceItem;
  onDone: () => void;
}) {
  const queryClient = useQueryClient();

  const finish = (ok: boolean, message: string) => {
    void queryClient.invalidateQueries({ queryKey: ["scales", "cycles"] });
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
    mutationFn: () => scalesApi.approveItem(cycleId, item.id),
    onSuccess: (result) =>
      finish(
        result.status === "approved" || result.status === "already_approved",
        result.status === "approved"
          ? result.detail || "Item approved."
          : result.detail,
      ),
    onError: (err) => {
      haptics.error();
      toast.error(getErrorMessage(err));
    },
  });

  const reject = useMutation({
    mutationFn: (reason: string) =>
      scalesApi.rejectItem(cycleId, item.id, reason),
    onSuccess: () => finish(true, "Item rejected."),
    onError: (err) => {
      haptics.error();
      toast.error(getErrorMessage(err));
    },
  });

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge variant="secondary">
          {item.action === "cancel" ? "Cancel task" : "Re-prioritize"}
        </Badge>
        {item.new_priority != null && (
          <Badge>
            → P{item.new_priority} · {PRIORITY_LABELS[item.new_priority] ?? "?"}
          </Badge>
        )}
      </div>

      <p className="text-sm leading-relaxed">{item.target_task_title}</p>

      <TgSection title="Why">
        <p className="text-xs text-muted-foreground">{item.rationale}</p>
      </TgSection>

      <PrimaryAction
        text={
          item.action === "cancel" ? "Approve → cancel task" : "Approve → apply"
        }
        loading={approve.isPending}
        onClick={() => approve.mutate()}
      />
      <RejectForm
        minChars={MIN_REJECT_CHARS}
        placeholder="Why leave it as is?"
        pending={reject.isPending}
        onSubmit={(reason) => reject.mutate(reason)}
      />
    </div>
  );
}
