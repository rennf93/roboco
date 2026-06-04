"use client";

import { useCallback } from "react";
import { useForm, type Resolver } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Plus, Trash2, Pencil, Lock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { Team, TaskType, Complexity, TaskNature } from "@/types";
import { usePrompterStore, type DraftState } from "@/store/prompter-store";
import type { TaskCreate } from "@/types";

// =============================================================================
// Zod Schema — mirrors TaskCreate constraints
// =============================================================================

export const draftSchema = z.object({
  title: z
    .string()
    .min(1, "Title is required")
    .max(200, "Title must be 200 characters or fewer"),
  description: z
    .string()
    .min(1, "Description is required")
    .max(5000, "Description must be 5000 characters or fewer"),
  team: z.nativeEnum(Team, { message: "Select a team" }),
  priority: z
    .number()
    .int()
    .min(0, "Priority must be 0–3")
    .max(3, "Priority must be 0–3"),
  acceptance_criteria: z
    .array(z.string().min(1, "Criterion cannot be empty"))
    .min(1, "At least one acceptance criterion is required"),
  task_type: z.nativeEnum(TaskType),
  estimated_complexity: z.nativeEnum(Complexity).nullable().optional(),
  nature: z.nativeEnum(TaskNature).nullable().optional(),
});

export type DraftFormValues = z.infer<typeof draftSchema>;

// =============================================================================
// Helpers
// =============================================================================

const PRIORITY_LABELS: Record<number, string> = {
  0: "P0 — Urgent",
  1: "P1 — High",
  2: "P2 — Medium",
  3: "P3 — Low",
};

function draftToForm(draft: DraftState): DraftFormValues {
  return {
    title: draft.title.value,
    description: draft.description.value,
    team: draft.team.value ?? Team.FRONTEND,
    priority: draft.priority.value,
    acceptance_criteria:
      draft.acceptance_criteria.value.length > 0
        ? draft.acceptance_criteria.value
        : [""],
    task_type: draft.task_type.value,
    estimated_complexity: draft.estimated_complexity.value ?? null,
    nature: draft.nature.value ?? null,
  };
}

// =============================================================================
// DirtyBadge — shows whether a field was user-edited or LLM-populated
// =============================================================================

function DirtyBadge({ dirty }: { dirty: boolean }) {
  if (dirty) {
    return (
      <span
        title="You edited this field — LLM updates are paused for it"
        className="inline-flex items-center gap-1 text-xs text-amber-600 font-medium"
      >
        <Lock className="h-3 w-3" />
        edited
      </span>
    );
  }
  return (
    <span
      title="This field will be updated by the AI"
      className="inline-flex items-center gap-1 text-xs text-muted-foreground"
    >
      <Pencil className="h-3 w-3" />
      AI
    </span>
  );
}

// =============================================================================
// DraftPanel Component
// =============================================================================

interface DraftPanelProps {
  /** Called when the form is valid and the user clicks Review & Launch */
  onLaunch: (values: TaskCreate) => void;
}

export function DraftPanel({ onLaunch }: DraftPanelProps) {
  const conv = usePrompterStore((s) => s.getActiveConversation());
  const setFieldFromUser = usePrompterStore((s) => s.setFieldFromUser);
  const setShowLaunchSummary = usePrompterStore((s) => s.setShowLaunchSummary);

  const draft = conv?.draft;

  const {
    register,
    handleSubmit,
    setValue,
    watch,
    formState: { errors, isValid },
  } = useForm<DraftFormValues>({
    resolver: zodResolver(draftSchema) as Resolver<DraftFormValues>,
    defaultValues: draft ? draftToForm(draft) : undefined,
    mode: "onChange",
    values: draft ? draftToForm(draft) : undefined,
  });

  // Watch the criteria array so we can render it reactively
  const watchedCriteria = watch("acceptance_criteria") ?? [];

  const handleFieldChange = useCallback(
    <K extends keyof DraftState>(
      field: K,
      value: DraftState[K]["value"]
    ) => {
      setFieldFromUser(field, value);
    },
    [setFieldFromUser]
  );

  const onSubmit = (values: DraftFormValues) => {
    const taskCreate: TaskCreate = {
      title: values.title,
      description: values.description,
      team: values.team,
      priority: values.priority,
      acceptance_criteria: values.acceptance_criteria.filter((c) => c.trim()),
      task_type: values.task_type,
      estimated_complexity: values.estimated_complexity ?? undefined,
      nature: values.nature ?? undefined,
    };
    onLaunch(taskCreate);
    setShowLaunchSummary(true);
  };

  const updateCriteria = useCallback(
    (updatedList: string[]) => {
      setValue("acceptance_criteria", updatedList, { shouldValidate: true });
      handleFieldChange("acceptance_criteria", updatedList);
    },
    [setValue, handleFieldChange]
  );

  if (!draft) {
    return (
      <div className="flex items-center justify-center h-full text-sm text-muted-foreground">
        Start a conversation to see the draft here.
      </div>
    );
  }

  return (
    <form
      onSubmit={handleSubmit(onSubmit)}
      className="flex flex-col gap-5 p-4 overflow-auto h-full"
    >
      <div className="flex items-center justify-between">
        <h3 className="font-semibold text-sm">Task Draft</h3>
        <span className="text-xs text-muted-foreground flex items-center gap-1">
          <Lock className="h-3 w-3" /> = protected
        </span>
      </div>

      {/* Title */}
      <div className="space-y-1">
        <div className="flex items-center justify-between">
          <Label htmlFor="draft-title" className="text-xs font-medium">
            Title *
          </Label>
          <DirtyBadge dirty={draft.title.dirty} />
        </div>
        <Input
          id="draft-title"
          {...register("title")}
          placeholder="Task title"
          onChange={(e) => {
            register("title").onChange(e);
            handleFieldChange("title", e.target.value);
          }}
          className={cn(errors.title && "border-destructive")}
        />
        {errors.title && (
          <p className="text-xs text-destructive">{errors.title.message}</p>
        )}
      </div>

      {/* Description */}
      <div className="space-y-1">
        <div className="flex items-center justify-between">
          <Label htmlFor="draft-description" className="text-xs font-medium">
            Description *
          </Label>
          <DirtyBadge dirty={draft.description.dirty} />
        </div>
        <textarea
          id="draft-description"
          {...register("description")}
          placeholder="What needs to be done…"
          rows={4}
          onChange={(e) => {
            register("description").onChange(e);
            handleFieldChange("description", e.target.value);
          }}
          className={cn(
            "flex w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring resize-none",
            errors.description && "border-destructive"
          )}
        />
        {errors.description && (
          <p className="text-xs text-destructive">{errors.description.message}</p>
        )}
      </div>

      {/* Team + Priority */}
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <Label className="text-xs font-medium">Team *</Label>
            <DirtyBadge dirty={draft.team.dirty} />
          </div>
          <Select
            value={draft.team.value ?? ""}
            onValueChange={(v) => {
              setValue("team", v as Team, { shouldValidate: true });
              handleFieldChange("team", v as Team);
            }}
          >
            <SelectTrigger
              className={cn(errors.team && "border-destructive")}
            >
              <SelectValue placeholder="Select team" />
            </SelectTrigger>
            <SelectContent>
              {Object.values(Team).map((t) => (
                <SelectItem key={t} value={t}>
                  {t.charAt(0).toUpperCase() + t.slice(1).replace("_", " ")}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {errors.team && (
            <p className="text-xs text-destructive">{errors.team.message}</p>
          )}
        </div>

        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <Label className="text-xs font-medium">Priority</Label>
            <DirtyBadge dirty={draft.priority.dirty} />
          </div>
          <Select
            value={String(draft.priority.value)}
            onValueChange={(v) => {
              setValue("priority", Number(v), { shouldValidate: true });
              handleFieldChange("priority", Number(v));
            }}
          >
            <SelectTrigger>
              <SelectValue placeholder="Priority" />
            </SelectTrigger>
            <SelectContent>
              {[0, 1, 2, 3].map((p) => (
                <SelectItem key={p} value={String(p)}>
                  {PRIORITY_LABELS[p]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {errors.priority && (
            <p className="text-xs text-destructive">{errors.priority.message}</p>
          )}
        </div>
      </div>

      {/* Task Type + Complexity */}
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <Label className="text-xs font-medium">Type</Label>
            <DirtyBadge dirty={draft.task_type.dirty} />
          </div>
          <Select
            value={draft.task_type.value}
            onValueChange={(v) => {
              setValue("task_type", v as TaskType, { shouldValidate: true });
              handleFieldChange("task_type", v as TaskType);
            }}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Object.values(TaskType).map((t) => (
                <SelectItem key={t} value={t}>
                  {t.charAt(0).toUpperCase() + t.slice(1)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <Label className="text-xs font-medium">Complexity</Label>
            <DirtyBadge dirty={draft.estimated_complexity.dirty} />
          </div>
          <Select
            value={draft.estimated_complexity.value ?? ""}
            onValueChange={(v) => {
              const val = (v || null) as Complexity | null;
              setValue("estimated_complexity", val, {
                shouldValidate: true,
              });
              handleFieldChange("estimated_complexity", val);
            }}
          >
            <SelectTrigger>
              <SelectValue placeholder="Complexity" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="">Unset</SelectItem>
              {Object.values(Complexity).map((c) => (
                <SelectItem key={c} value={c}>
                  {c.charAt(0).toUpperCase() + c.slice(1)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {/* Acceptance Criteria */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <Label className="text-xs font-medium">
            Acceptance Criteria *{" "}
            <span className="text-muted-foreground font-normal">(min. 1)</span>
          </Label>
          <DirtyBadge dirty={draft.acceptance_criteria.dirty} />
        </div>
        <div className="space-y-2">
          {watchedCriteria.map((criterion, index) => (
            <div key={index} className="flex items-center gap-2">
              <Input
                value={criterion}
                placeholder={`Criterion ${index + 1}`}
                onChange={(e) => {
                  const updated = [...watchedCriteria];
                  updated[index] = e.target.value;
                  updateCriteria(updated);
                }}
                className={cn(
                  errors.acceptance_criteria?.[index] && "border-destructive"
                )}
              />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                onClick={() => {
                  const updated = watchedCriteria.filter((_, i) => i !== index);
                  updateCriteria(updated.length > 0 ? updated : [""]);
                }}
                disabled={watchedCriteria.length === 1}
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>
          ))}
          {typeof errors.acceptance_criteria?.message === "string" && (
            <p className="text-xs text-destructive">
              {errors.acceptance_criteria.message}
            </p>
          )}
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="w-full"
            onClick={() => updateCriteria([...watchedCriteria, ""])}
          >
            <Plus className="h-4 w-4 mr-1" />
            Add criterion
          </Button>
        </div>
      </div>

      {/* Submit */}
      <Button type="submit" disabled={!isValid} className="w-full mt-auto">
        Review & Launch →
      </Button>
    </form>
  );
}
