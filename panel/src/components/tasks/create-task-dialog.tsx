"use client";

import { useState } from "react";
import { useCreateTask } from "@/hooks/use-tasks";
import { useProducts } from "@/hooks/use-products";
import { Team, Complexity, TaskStatus, TaskNature, TaskType } from "@/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
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
import { Plus, ChevronDown, ChevronRight, GitBranch } from "lucide-react";
import { toast } from "sonner";
import { AcceptanceCriteriaEditor } from "./acceptance-criteria-editor";
import { MarkdownEditor } from "./markdown-editor";
import { DependencySelector } from "./dependency-selector";
import { TaskSelector } from "./task-selector";
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

// Nature options (technical vs non-technical)
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
  [TaskType.DOCUMENTATION]: "Documentation updates. Follows the full git workflow.",
  [TaskType.RESEARCH]:
    "Research findings, committed as notes. Follows the full git workflow.",
  [TaskType.PLANNING]:
    "Plans or architecture, committed as docs. Follows the full git workflow.",
  [TaskType.DESIGN]:
    "Designs or specs, committed as assets. Follows the full git workflow.",
  [TaskType.ADMINISTRATIVE]:
    "Process docs, committed as notes. Follows the full git workflow.",
};

// Initial status options (only PENDING and BACKLOG for creation)
const STATUS_OPTIONS = [
  { value: TaskStatus.PENDING, label: "Pending (Ready for work)" },
  { value: TaskStatus.BACKLOG, label: "Backlog (PM setup)" },
];

interface FormErrors {
  title?: string;
  description?: string;
  acceptance_criteria?: string;
  project_id?: string;
}

export function CreateTaskDialog() {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [team, setTeam] = useState<Team>(Team.BACKEND);
  const [priority, setPriority] = useState<number>(2);
  const [complexity, setComplexity] = useState<Complexity>(Complexity.MEDIUM);
  const [nature, setNature] = useState<TaskNature>(TaskNature.TECHNICAL);
  const [status, setStatus] = useState<TaskStatus>(TaskStatus.PENDING);
  const [acceptanceCriteria, setAcceptanceCriteria] = useState<string[]>([]);
  const [dependencyIds, setDependencyIds] = useState<string[]>([]);
  const [parentTaskId, setParentTaskId] = useState<string | null>(null);
  const [assignedTo, setAssignedTo] = useState<string | null>(null);
  const [taskType, setTaskType] = useState<TaskType>(TaskType.CODE);
  const [projectId, setProjectId] = useState<string>("");
  const [productId, setProductId] = useState<string>("");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [errors, setErrors] = useState<FormErrors>({});

  const createTask = useCreateTask();
  const { data: products = [] } = useProducts();

  const validate = (): boolean => {
    const newErrors: FormErrors = {};

    if (!title.trim() || title.trim().length < 5) {
      newErrors.title = "Title must be at least 5 characters";
    }
    if (title.trim().length > 200) {
      newErrors.title = "Title must be less than 200 characters";
    }

    if (!description.trim() || description.trim().length < 20) {
      newErrors.description = "Description must be at least 20 characters";
    }

    if (acceptanceCriteria.length === 0) {
      newErrors.acceptance_criteria =
        "At least one acceptance criterion is required";
    }

    if (!projectId && !productId) {
      newErrors.project_id =
        "Pick a Project (the repo) or a Product (cell→project map for a fan-out task)";
    }

    // A task targets exactly one repo OR fans out via a Product — never both.
    // The server silently lets product_id win at routing and drops project_id,
    // so submitting both would record a misleading, never-used project_id. The
    // selectors clear each other on pick (below); this validator is the
    // backstop for any path that sets both.
    if (projectId && productId) {
      newErrors.project_id =
        "Pick either a Project or a Product, not both — a task targets one repo or fans out via a Product";
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!validate()) {
      return;
    }

    try {
      await createTask.mutateAsync({
        title: title.trim(),
        description: description.trim(),
        team,
        priority,
        status,
        acceptance_criteria: acceptanceCriteria
          .map((c) => c.trim())
          .filter(Boolean),
        estimated_complexity: complexity,
        nature,
        task_type: taskType,
        ...(projectId && { project_id: projectId }),
        ...(productId && { product_id: productId }),
        ...(dependencyIds.length > 0 && { dependency_ids: dependencyIds }),
        ...(parentTaskId && { parent_task_id: parentTaskId }),
        ...(assignedTo && { assigned_to: assignedTo }),
      });
      toast.success("Task created successfully");
      setOpen(false);
      resetForm();
    } catch {
      toast.error("Failed to create task");
    }
  };

  const resetForm = () => {
    setTitle("");
    setDescription("");
    setTeam(Team.BACKEND);
    setPriority(2);
    setComplexity(Complexity.MEDIUM);
    setNature(TaskNature.TECHNICAL);
    setStatus(TaskStatus.PENDING);
    setAcceptanceCriteria([]);
    setDependencyIds([]);
    setParentTaskId(null);
    setAssignedTo(null);
    setTaskType(TaskType.CODE);
    setProjectId("");
    setProductId("");
    setAdvancedOpen(false);
    setErrors({});
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(newOpen) => {
        setOpen(newOpen);
        if (!newOpen) resetForm();
      }}
    >
      <DialogTrigger asChild>
        <Button>
          <Plus className="h-4 w-4 mr-2" />
          New Task
        </Button>
      </DialogTrigger>
      <DialogContent className="w-full max-w-[95vw] sm:max-w-xl md:max-w-3xl lg:max-w-5xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Create New Task</DialogTitle>
          <DialogDescription>
            Create a new task with clear requirements and acceptance criteria.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-6 mt-4">
          {/* Title */}
          <div className="space-y-2">
            <HelpTip label="5-200 characters. Shown in the task list, task detail, and used as the default PR title.">
              <Label htmlFor="title">
                Title <span className="text-destructive">*</span>
              </Label>
            </HelpTip>
            <Input
              id="title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Enter task title (5-200 characters)"
              className={errors.title ? "border-destructive" : ""}
            />
            {errors.title && (
              <p className="text-xs text-destructive">{errors.title}</p>
            )}
          </div>

          {/* Description */}
          <MarkdownEditor
            label="Description"
            value={description}
            onChange={setDescription}
            placeholder="Describe the task in detail. Include context, requirements, and any relevant information..."
            required
            minLength={20}
            error={errors.description}
          />

          {/* Team, Priority, Complexity, Status, Nature */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4">
            <div className="space-y-2">
              <HelpTip label="Which cell owns this task — governs claim eligibility and the branch name (feature/{team}/...).">
                <Label>
                  Team <span className="text-destructive">*</span>
                </Label>
              </HelpTip>
              <Select value={team} onValueChange={(v) => setTeam(v as Team)}>
                <SelectTrigger className="w-full">
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
              <HelpTip label="Pending is claimable immediately; Backlog waits for PM setup (dependencies/session) before it can be activated.">
                <Label>Status</Label>
              </HelpTip>
              <Select
                value={status}
                onValueChange={(v) => setStatus(v as TaskStatus)}
              >
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {STATUS_OPTIONS.map((s) => (
                    <SelectItem key={s.value} value={s.value}>
                      {s.label}
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
                <SelectTrigger className="w-full">
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
                <SelectTrigger className="w-full">
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
                <SelectTrigger className="w-full">
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
            error={errors.acceptance_criteria}
          />

          {/* Dependencies */}
          <DependencySelector
            selectedIds={dependencyIds}
            onChange={setDependencyIds}
          />

          {/* Advanced Options */}
          <Collapsible open={advancedOpen} onOpenChange={setAdvancedOpen}>
            <CollapsibleTrigger asChild>
              <Button
                variant="ghost"
                type="button"
                className="w-full justify-between"
              >
                <HelpTip label="Parent task, agent assignment, task type, and repo/product routing.">
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
              {/* Parent Task */}
              <div className="space-y-2">
                <HelpTip label="Nests this task under another — its claim order is then gated by sequence relative to siblings under the same parent.">
                  <Label>Parent Task (for subtasks)</Label>
                </HelpTip>
                <TaskSelector
                  value={parentTaskId}
                  onChange={setParentTaskId}
                  placeholder="Select parent task (optional)..."
                  filterByTeam={team}
                />
                <p className="text-xs text-muted-foreground">
                  Make this task a subtask of an existing task
                </p>
              </div>

              {/* Assign To */}
              <div className="space-y-2">
                <HelpTip label="Pin a specific agent; leave unassigned to let the orchestrator route by role, team, and availability.">
                  <Label>Assign To</Label>
                </HelpTip>
                <AgentSelector
                  value={assignedTo}
                  onChange={setAssignedTo}
                  placeholder="Unassigned (orchestrator will route)"
                  filterByTeam={team}
                />
                <p className="text-xs text-muted-foreground">
                  Leave unassigned to let the orchestrator route automatically,
                  or manually assign to a specific agent
                </p>
              </div>

              {/* Git Configuration Section */}
              <div className="space-y-4 pt-4 border-t">
                <div className="flex items-center gap-2 mb-2">
                  <GitBranch className="h-4 w-4 text-muted-foreground" />
                  <HelpTip label="Every task type follows the same branch → commits → PR workflow; these fields just route it.">
                    <span className="font-medium text-sm">
                      Git & Work Configuration
                    </span>
                  </HelpTip>
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
                    <SelectTrigger className="w-full">
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
                  <p className="text-xs text-muted-foreground">
                    Type of work: code, documentation, research, etc.
                  </p>
                </div>

                {/* Project — required UNLESS a Product is picked (fan-out task) */}
                <div className="space-y-2">
                  <HelpTip label="The repo the branch/PR are opened against once this task is claimed.">
                    <Label>Project</Label>
                  </HelpTip>
                  <ProjectSelector
                    value={projectId || null}
                    onChange={(value) => setProjectId(value || "")}
                    placeholder="Select project..."
                  />
                  {errors.project_id && (
                    <p className="text-xs text-destructive">
                      {errors.project_id}
                    </p>
                  )}
                  <p className="text-xs text-muted-foreground">
                    The repo this task targets. Optional if you pick a Product
                    below — a fan-out task routes each cell&apos;s subtask via
                    the Product instead.
                  </p>
                </div>

                {/* Product (optional) — drives per-cell project routing of subtasks */}
                <div className="space-y-2">
                  <HelpTip label="Fan-out: each delegated subtask routes to that cell's own mapped project instead of one repo.">
                    <Label>Product</Label>
                  </HelpTip>
                  <Select
                    value={productId || "none"}
                    onValueChange={(v) => setProductId(v === "none" ? "" : v)}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue placeholder="None" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="none">
                        None (single project)
                      </SelectItem>
                      {products.map((p) => (
                        <SelectItem key={p.id} value={p.id}>
                          {p.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <p className="text-xs text-muted-foreground">
                    Optional. When set, delegated subtasks route to each
                    cell&apos;s mapped project (manage these in Products).
                  </p>
                </div>
              </div>
            </CollapsibleContent>
          </Collapsible>

          {/* Actions */}
          <div className="flex justify-end gap-2 pt-4 border-t">
            <HelpTip label="Discards everything entered above without creating a task.">
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  setOpen(false);
                  resetForm();
                }}
              >
                Cancel
              </Button>
            </HelpTip>
            <HelpTip label="Validates the required fields, then creates the task via the API.">
              <span
                className="inline-block"
                tabIndex={createTask.isPending ? 0 : undefined}
              >
                <Button type="submit" disabled={createTask.isPending}>
                  {createTask.isPending ? "Creating..." : "Create Task"}
                </Button>
              </span>
            </HelpTip>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
