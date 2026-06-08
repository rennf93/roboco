"use client";

import { AlertTriangle, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Team, TaskType, Complexity } from "@/types";
import type { EditableDraft, TargetKind } from "@/hooks/use-prompter";
import { useProjects } from "@/hooks/use-projects";
import { useProducts } from "@/hooks/use-products";
import { StringListEditor } from "./string-list-editor";
import { TheWorkEditor } from "./the-work-editor";

interface ConfirmDialogProps {
  open: boolean;
  draft: EditableDraft;
  onClose: () => void;
  onUpdate: (updates: Partial<EditableDraft>) => void;
  onConfirm: () => Promise<void> | void;
  isLaunching: boolean;
  isValid: boolean;
}

const WARNING_BANNER_ID = "prompter-warning-banner";

// 0 is the highest priority, 3 the lowest — matches the backend contract.
const PRIORITY_OPTIONS: { value: string; label: string }[] = [
  { value: "0", label: "Urgent (P0)" },
  { value: "1", label: "High (P1)" },
  { value: "2", label: "Medium (P2)" },
  { value: "3", label: "Low (P3)" },
];

const CELL_TEAMS: Team[] = [Team.BACKEND, Team.FRONTEND, Team.UX_UI];

export function ConfirmDialog({
  open,
  draft,
  onClose,
  onUpdate,
  onConfirm,
  isLaunching,
  isValid,
}: ConfirmDialogProps) {
  const { data: projects = [] } = useProjects();
  const { data: products = [] } = useProducts();

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o && !isLaunching) onClose();
      }}
    >
      <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Review &amp; Launch Task</DialogTitle>
        </DialogHeader>

        {/* Warning banner */}
        <div
          id={WARNING_BANNER_ID}
          className="flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            This creates a real task and notifies the team. It cannot be undone
            from this screen.
          </span>
        </div>

        {/* Fields — NOT wrapped in a <form> to prevent Enter-key submission */}
        <div className="space-y-5 py-2">
          {/* Title */}
          <div className="space-y-1.5">
            <Label htmlFor="prompter-title">
              Title <span className="text-destructive">*</span>
            </Label>
            <Input
              id="prompter-title"
              value={draft.title}
              onChange={(e) => onUpdate({ title: e.target.value })}
              placeholder="Task title"
              disabled={isLaunching}
            />
          </div>

          {/* Objective */}
          <div className="space-y-1.5">
            <Label htmlFor="prompter-objective">Objective</Label>
            <Textarea
              id="prompter-objective"
              value={draft.objective}
              onChange={(e) => onUpdate({ objective: e.target.value })}
              placeholder="The outcome this task delivers…"
              rows={2}
              disabled={isLaunching}
            />
          </div>

          {/* What This Builds */}
          <StringListEditor
            label="What This Builds"
            items={draft.what_this_builds}
            onChange={(what_this_builds) => onUpdate({ what_this_builds })}
            placeholder="Add an artifact…"
            disabled={isLaunching}
          />

          {/* The Work (per-cell) */}
          <TheWorkEditor
            cells={draft.the_work}
            onChange={(the_work) => onUpdate({ the_work })}
            disabled={isLaunching}
          />

          {/* Notes */}
          <StringListEditor
            label="Notes"
            items={draft.notes}
            onChange={(notes) => onUpdate({ notes })}
            placeholder="Add a constraint or reuse pointer…"
            disabled={isLaunching}
          />

          {/* Success Criteria (the task's acceptance criteria) */}
          <StringListEditor
            label="Success Criteria"
            items={draft.acceptance_criteria}
            onChange={(acceptance_criteria) => onUpdate({ acceptance_criteria })}
            placeholder="Add a verifiable criterion…"
            disabled={isLaunching}
          />

          {/* Target: single-cell project vs board-led product */}
          <div className="space-y-2">
            <Label>
              Target <span className="text-destructive">*</span>
            </Label>
            <Tabs
              value={draft.targetKind}
              onValueChange={(v) => onUpdate({ targetKind: v as TargetKind })}
            >
              <TabsList className="grid w-full grid-cols-2">
                <TabsTrigger value="project" disabled={isLaunching}>
                  Single cell (Project)
                </TabsTrigger>
                <TabsTrigger value="product" disabled={isLaunching}>
                  Board-led (Product)
                </TabsTrigger>
              </TabsList>
            </Tabs>

            {draft.targetKind === "project" ? (
              <Select
                value={draft.projectId}
                onValueChange={(v) => onUpdate({ projectId: v })}
                disabled={isLaunching}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select a project…" />
                </SelectTrigger>
                <SelectContent>
                  {projects.map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              <>
                <Select
                  value={draft.productId}
                  onValueChange={(v) => onUpdate({ productId: v })}
                  disabled={isLaunching}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Select a product…" />
                  </SelectTrigger>
                  <SelectContent>
                    {products.map((p) => (
                      <SelectItem key={p.id} value={p.id}>
                        {p.name} ({p.cell_count} cells)
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {products.length === 0 && (
                  <p className="text-xs text-muted-foreground">
                    No products exist yet. A board-led feature needs a product
                    (a cell→repo map) — create one under Products, or target a
                    single project instead.
                  </p>
                )}
              </>
            )}
          </div>

          {/* Metadata row */}
          <div className="grid grid-cols-3 gap-4">
            {/* Team — only meaningful for a single-cell project task */}
            {draft.targetKind === "project" && (
              <div className="space-y-1.5">
                <Label>
                  Cell <span className="text-destructive">*</span>
                </Label>
                <Select
                  value={draft.team}
                  onValueChange={(v) => onUpdate({ team: v as Team })}
                  disabled={isLaunching}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Select cell" />
                  </SelectTrigger>
                  <SelectContent>
                    {CELL_TEAMS.map((t) => (
                      <SelectItem key={t} value={t}>
                        {t.replace("_", " ")}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}

            {/* Priority */}
            <div className="space-y-1.5">
              <Label>Priority</Label>
              <Select
                value={String(draft.priority)}
                onValueChange={(v) => onUpdate({ priority: Number(v) })}
                disabled={isLaunching}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PRIORITY_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Task Type */}
            <div className="space-y-1.5">
              <Label>Type</Label>
              <Select
                value={draft.task_type || ""}
                onValueChange={(v) => onUpdate({ task_type: v as TaskType })}
                disabled={isLaunching}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select type" />
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
          </div>

          {/* Complexity */}
          <div className="space-y-1.5">
            <Label>Estimated Complexity</Label>
            <Select
              value={draft.estimated_complexity || ""}
              onValueChange={(v) =>
                onUpdate({ estimated_complexity: v as Complexity })
              }
              disabled={isLaunching}
            >
              <SelectTrigger className="w-48">
                <SelectValue placeholder="Select complexity" />
              </SelectTrigger>
              <SelectContent>
                {Object.values(Complexity).map((c) => (
                  <SelectItem key={c} value={c}>
                    {c.charAt(0).toUpperCase() + c.slice(1)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <DialogFooter className="gap-2">
          <Button variant="outline" onClick={onClose} disabled={isLaunching}>
            Back
          </Button>
          <Button
            onClick={onConfirm}
            disabled={!isValid || isLaunching}
            aria-describedby={WARNING_BANNER_ID}
          >
            {isLaunching ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Launching…
              </>
            ) : (
              "Confirm & Launch"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
