"use client";

import { useState } from "react";
import { useUpdateTask } from "@/hooks/use-tasks";
import { Task, Team, Complexity, TaskNature, TaskType } from "@/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { ChevronDown, ChevronRight, GitBranch } from "lucide-react";
import { toast } from "sonner";
import { MarkdownEditor } from "./markdown-editor";
import { AcceptanceCriteriaEditor } from "./acceptance-criteria-editor";
import { AgentSelector } from "@/components/agents/agent-selector";
import { ProjectSelector } from "@/components/projects/project-selector";
import { HelpTip } from "@/components/ui/help-tip";

// Priority options (0=P0 highest, 3=P3 lowest)
const PRIORITY_OPTIONS = [
  { value: 0, label: "P0 - Highest" },
  { value: 1, label: "P1 - High" },
  { value: 2, label: "P2 - Medium" },
  { value: 3, label: "P3 - Low" },
];

// Complexity options
const COMPLEXITY_OPTIONS = [
  { value: Complexity.LOW, label: "Low" },
  { value: Complexity.MEDIUM, label: "Medium" },
  { value: Complexity.HIGH, label: "High" },
];

// Nature options
const NATURE_OPTIONS = [
  { value: TaskNature.TECHNICAL, label: "Technical" },
  { value: TaskNature.NON_TECHNICAL, label: "Non-Technical" },
];

// Task type options
const TASK_TYPE_OPTIONS = [
  { value: TaskType.CODE, label: "Code" },
  { value: TaskType.DOCUMENTATION, label: "Documentation" },
  { value: TaskType.RESEARCH, label: "Research" },
  { value: TaskType.PLANNING, label: "Planning" },
  { value: TaskType.DESIGN, label: "Design" },
  { value: TaskType.ADMINISTRATIVE, label: "Administrative" },
];

// What each task type produces. All types follow the same full git workflow
// (branch, commits, PR) — this only classifies the kind of artifact.
const TASK_TYPE_DESCRIPTIONS: Record<TaskType, string> = {
  [TaskType.CODE]: "Source code changes. Follows the full git workflow.",
  [TaskType.DOCUMENTATION]:
    "Documentation updates. Follows the full git workflow.",
  [TaskType.RESEARCH]:
    "Research findings, committed as notes. Follows the full git workflow.",
  [TaskType.PLANNING]:
    "Plans or architecture, committed as docs. Follows the full git workflow.",
  [TaskType.DESIGN]:
    "Designs or specs, committed as assets. Follows the full git workflow.",
  [TaskType.ADMINISTRATIVE]:
    "Process docs, committed as notes. Follows the full git workflow.",
};

interface EditTaskDialogProps {
  task: Task;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

// Inner component that resets when task.id changes via key
function EditTaskDialogInner({
  task,
  onOpenChange,
}: {
  task: Task;
  onOpenChange: (open: boolean) => void;
}) {
  const [title, setTitle] = useState(task.title);
  const [description, setDescription] = useState(task.description);
  const [team, setTeam] = useState<Team>(task.team);
  const [priority, setPriority] = useState<number>(task.priority);
  const [complexity, setComplexity] = useState<Complexity>(
    task.estimated_complexity,
  );
  const [nature, setNature] = useState<TaskNature>(
    task.nature ?? TaskNature.TECHNICAL,
  );
  const [acceptanceCriteria, setAcceptanceCriteria] = useState<string[]>(
    task.acceptance_criteria,
  );
  const [acError, setAcError] = useState<string | undefined>();
  const [taskType, setTaskType] = useState<TaskType>(
    task.task_type ?? TaskType.CODE,
  );
  const [projectId, setProjectId] = useState<string>(task.project_id ?? "");
  const [assignedTo, setAssignedTo] = useState<string | null>(
    task.assigned_to ?? null,
  );
  const [targetDate, setTargetDate] = useState<string>(
    task.target_date
      ? new Date(task.target_date).toISOString().slice(0, 16)
      : "",
  );
  const [budgetUsd, setBudgetUsd] = useState<string>(
    task.budget_usd != null ? String(task.budget_usd) : "",
  );
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const updateTask = useUpdateTask();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!title.trim() || title.trim().length < 5) {
      toast.error("Title must be at least 5 characters");
      return;
    }

    if (!description.trim() || description.trim().length < 20) {
      toast.error("Description must be at least 20 characters");
      return;
    }

    if (acceptanceCriteria.length === 0) {
      setAcError("At least one acceptance criterion is required");
      return;
    }
    setAcError(undefined);

    const trimmedBudget = budgetUsd.trim();
    const parsedBudget = trimmedBudget ? Number(trimmedBudget) : null;
    if (trimmedBudget && (Number.isNaN(parsedBudget) || parsedBudget! <= 0)) {
      toast.error(
        "Budget must be greater than 0 — leave it empty for the task-type default",
      );
      return;
    }

    const trimmedCriteria = acceptanceCriteria
      .map((c) => c.trim())
      .filter(Boolean);
    const criteriaChanged =
      JSON.stringify(trimmedCriteria) !==
      JSON.stringify(task.acceptance_criteria);

    try {
      await updateTask.mutateAsync({
        taskId: task.id,
        updates: {
          title: title.trim(),
          description: description.trim(),
          team,
          priority,
          estimated_complexity: complexity,
          nature,
          task_type: taskType,
          project_id: projectId,
          assigned_to: assignedTo,
          target_date: targetDate ? new Date(targetDate).toISOString() : null,
          budget_usd: parsedBudget,
          ...(criteriaChanged && { acceptance_criteria: trimmedCriteria }),
        },
      });
      toast.success("Task updated successfully");
      onOpenChange(false);
    } catch {
      toast.error("Failed to update task");
    }
  };

  return (
    <Dialog open={true} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Edit Task</DialogTitle>
          <DialogDescription>
            Update task details. Some fields may be locked based on task status.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-6 mt-4">
          {/* Title */}
          <div className="space-y-2">
            <HelpTip label="5-200 characters. Shown in the task list, task detail, and used as the default PR title.">
              <Label htmlFor="edit-title">Title</Label>
            </HelpTip>
            <Input
              id="edit-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Enter task title"
            />
          </div>

          {/* Description */}
          <MarkdownEditor
            label="Description"
            value={description}
            onChange={setDescription}
            placeholder="Describe the task in detail..."
            minLength={20}
          />

          {/* Team, Priority, Complexity, Nature */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div className="space-y-2">
              <HelpTip label="Which cell owns this task — governs claim eligibility. Changing it after a branch exists doesn't rename the branch.">
                <Label>Team</Label>
              </HelpTip>
              <Select value={team} onValueChange={(v) => setTeam(v as Team)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.values(Team).map((t) => (
                    <SelectItem key={t} value={t}>
                      {t.replace(/_/g, " ")}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <HelpTip label="Orders claim/dispatch queues — P0 tasks surface first. Doesn't force who claims it or block lower priorities.">
                <Label>Priority</Label>
              </HelpTip>
              <Select
                value={String(priority)}
                onValueChange={(v) => setPriority(parseInt(v, 10))}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PRIORITY_OPTIONS.map((p) => (
                    <SelectItem key={p.value} value={String(p.value)}>
                      {p.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <HelpTip label="Medium/High auto-routes a code task to the cell or Main PM to break down; a bare High dev task with no subtasks self-blocks.">
                <Label>Complexity</Label>
              </HelpTip>
              <Select
                value={complexity}
                onValueChange={(v) => setComplexity(v as Complexity)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {COMPLEXITY_OPTIONS.map((c) => (
                    <SelectItem key={c.value} value={c.value}>
                      {c.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <HelpTip label="Filtering only: Non-Technical root tasks awaiting PM review surface in the Board's strategic queue.">
                <Label>Nature</Label>
              </HelpTip>
              <Select
                value={nature}
                onValueChange={(v) => setNature(v as TaskNature)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {NATURE_OPTIONS.map((n) => (
                    <SelectItem key={n.value} value={n.value}>
                      {n.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          {/* Acceptance Criteria */}
          <AcceptanceCriteriaEditor
            criteria={acceptanceCriteria}
            onChange={setAcceptanceCriteria}
            error={acError}
          />

          {/* Advanced Options */}
          <Collapsible open={advancedOpen} onOpenChange={setAdvancedOpen}>
            <CollapsibleTrigger asChild>
              <Button
                variant="ghost"
                type="button"
                className="w-full justify-between"
              >
                <HelpTip label="Agent assignment, target date, task type, and repo routing.">
                  <span>Advanced Options</span>
                </HelpTip>
                {advancedOpen ? (
                  <ChevronDown className="h-4 w-4" />
                ) : (
                  <ChevronRight className="h-4 w-4" />
                )}
              </Button>
            </CollapsibleTrigger>
            <CollapsibleContent className="space-y-4 pt-4">
              {/* Assigned To */}
              <div className="space-y-2">
                <HelpTip label="Pin a specific agent; leave unassigned to let the orchestrator route by role, team, and availability.">
                  <Label>Assigned To</Label>
                </HelpTip>
                <AgentSelector
                  value={assignedTo}
                  onChange={setAssignedTo}
                  placeholder="Unassigned"
                  filterByTeam={team}
                />
              </div>

              {/* Target Date */}
              <div className="space-y-2">
                <HelpTip label="Informational deadline only — nothing in the lifecycle enforces or auto-escalates on it.">
                  <Label>Target Date</Label>
                </HelpTip>
                <Input
                  type="datetime-local"
                  value={targetDate}
                  onChange={(e) => setTargetDate(e.target.value)}
                />
              </div>

              {/* Budget (USD) */}
              <div className="space-y-2">
                <HelpTip label="Caps this task's own agent-spawn spend; only enforced when the task-budgets feature flag is on. Empty = use the task-type default.">
                  <Label>Budget (USD)</Label>
                </HelpTip>
                <Input
                  type="number"
                  min="0.01"
                  step="0.01"
                  placeholder="Task-type default"
                  value={budgetUsd}
                  onChange={(e) => setBudgetUsd(e.target.value)}
                />
                <p className="text-xs text-muted-foreground">
                  Must be greater than 0 — a 0 budget would block the task
                  before it spends a cent. Leave blank for the task-type
                  default.
                </p>
              </div>

              {/* Git Configuration Section */}
              <div className="space-y-4 pt-4 border-t">
                <div className="flex items-center gap-2 mb-2">
                  <GitBranch className="h-4 w-4 text-muted-foreground" />
                  <span className="font-medium text-sm">
                    Git & Work Configuration
                  </span>
                </div>

                {/* Task Type */}
                <div className="space-y-2">
                  <HelpTip label={TASK_TYPE_DESCRIPTIONS[taskType]}>
                    <Label>Task Type</Label>
                  </HelpTip>
                  <Select
                    value={taskType}
                    onValueChange={(v) => setTaskType(v as TaskType)}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {TASK_TYPE_OPTIONS.map((t) => (
                        <SelectItem key={t.value} value={t.value}>
                          {t.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                {/* Project Selector (required - all tasks follow git workflow) */}
                <div className="space-y-2">
                  <HelpTip label="The repo the branch/PR are opened against. Locked once a branch exists — see below.">
                    <Label>Project</Label>
                  </HelpTip>
                  <ProjectSelector
                    value={projectId || null}
                    onChange={(value) => setProjectId(value || "")}
                    placeholder="Select project..."
                    disabled={!!task.branch_name}
                  />
                  {task.branch_name && (
                    <p className="text-xs text-muted-foreground">
                      Project cannot be changed after work has started
                    </p>
                  )}
                </div>
              </div>
            </CollapsibleContent>
          </Collapsible>

          {/* Actions */}
          <div className="flex justify-end gap-2 pt-4 border-t">
            <HelpTip label="Discards any edits made above and closes without saving.">
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
              >
                Cancel
              </Button>
            </HelpTip>
            <HelpTip label="Sends the changed fields to the API and updates the task in place.">
              <span
                className="inline-block"
                tabIndex={updateTask.isPending ? 0 : undefined}
              >
                <Button type="submit" disabled={updateTask.isPending}>
                  {updateTask.isPending ? "Saving..." : "Save Changes"}
                </Button>
              </span>
            </HelpTip>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// Wrapper component that uses key to reset form when task changes
export function EditTaskDialog({
  task,
  open,
  onOpenChange,
}: EditTaskDialogProps) {
  if (!open) return null;
  return (
    <EditTaskDialogInner
      key={task.id}
      task={task}
      onOpenChange={onOpenChange}
    />
  );
}
