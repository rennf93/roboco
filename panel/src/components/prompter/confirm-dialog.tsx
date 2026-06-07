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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { AcceptanceCriteriaEditor } from "@/components/tasks/acceptance-criteria-editor";
import { MarkdownEditor } from "@/components/tasks/markdown-editor";
import { Team, TaskType, Complexity } from "@/types";
import type { EditableDraft } from "@/hooks/use-prompter";

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

export function ConfirmDialog({
  open,
  draft,
  onClose,
  onUpdate,
  onConfirm,
  isLaunching,
  isValid,
}: ConfirmDialogProps) {
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o && !isLaunching) onClose(); }}>
      <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Review &amp; Confirm Task</DialogTitle>
        </DialogHeader>

        {/* Warning banner */}
        <div
          id={WARNING_BANNER_ID}
          className="flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            This will create a real task and notify the team. It cannot be undone from this screen.
          </span>
        </div>

        {/* Form fields — NOT wrapped in a <form> to prevent Enter-key submission bypass */}
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

          {/* Description */}
          <MarkdownEditor
            label="Description"
            value={draft.description}
            onChange={(v) => onUpdate({ description: v })}
            placeholder="Describe what needs to be done…"
            required
            minLength={20}
          />

          {/* Acceptance Criteria */}
          <AcceptanceCriteriaEditor
            criteria={draft.acceptance_criteria}
            onChange={(criteria) => onUpdate({ acceptance_criteria: criteria })}
          />

          {/* Metadata row */}
          <div className="grid grid-cols-3 gap-4">
            {/* Team */}
            <div className="space-y-1.5">
              <Label>
                Team <span className="text-destructive">*</span>
              </Label>
              <Select
                value={draft.team}
                onValueChange={(v) => onUpdate({ team: v as Team })}
                disabled={isLaunching}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select team" />
                </SelectTrigger>
                <SelectContent>
                  {Object.values(Team).map((t) => (
                    <SelectItem key={t} value={t}>
                      {t.replace("_", " ")}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

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
                  <SelectItem value="0">Low</SelectItem>
                  <SelectItem value="1">Medium</SelectItem>
                  <SelectItem value="2">High</SelectItem>
                  <SelectItem value="3">Urgent</SelectItem>
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
              onValueChange={(v) => onUpdate({ estimated_complexity: v as Complexity })}
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
          <Button
            variant="outline"
            onClick={onClose}
            disabled={isLaunching}
          >
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
                Creating…
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
