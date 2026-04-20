"use client";

import { useState, useRef, useEffect } from "react";
import { Task, Complexity, TaskNature } from "@/types";
import { useUpdateTask } from "@/hooks/use-tasks";
import { useProject } from "@/hooks/use-projects";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { User, Calendar, Clock, Target, AlertTriangle, GitBranch, FolderGit2, Wrench, Briefcase, Hash, GitPullRequest, ExternalLink } from "lucide-react";
import { toast } from "sonner";
import { getAgentDisplayName, resolveToSlug } from "@/lib/agent-utils";
import { TaskTypeBadge } from "../task-type-badge";
import { DocsStatusBadge } from "../docs-status-badge";
import Link from "next/link";

// Priority colors
const priorityColors: Record<number, string> = {
  0: "bg-red-200 text-red-700 dark:bg-red-900 dark:text-red-200",
  1: "bg-orange-200 text-orange-700 dark:bg-orange-900 dark:text-orange-200",
  2: "bg-blue-200 text-blue-700 dark:bg-blue-900 dark:text-blue-200",
  3: "bg-gray-200 text-gray-700 dark:bg-gray-700 dark:text-gray-200",
};

const priorityLabels: Record<number, string> = {
  0: "P0 - Highest",
  1: "P1 - High",
  2: "P2 - Medium",
  3: "P3 - Low",
};

const complexityLabels: Record<string, string> = {
  [Complexity.LOW]: "Low",
  [Complexity.MEDIUM]: "Medium",
  [Complexity.HIGH]: "High",
};

interface TaskMetadataProps {
  task: Task;
}

function formatDate(date: string | null): string {
  if (!date) return "-";
  return new Date(date).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatRelativeTime(date: string | null): string {
  if (!date) return "-";
  const now = new Date();
  const d = new Date(date);
  const diffMs = now.getTime() - d.getTime();
  const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
  const diffDays = Math.floor(diffHours / 24);

  if (diffHours < 1) return "Just now";
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;
  return formatDate(date);
}

function formatDateForInput(date: string | null): string {
  if (!date) return "";
  const d = new Date(date);
  return d.toISOString().slice(0, 16); // Format: YYYY-MM-DDTHH:mm
}

export function TaskMetadata({ task }: TaskMetadataProps) {
  const updateTask = useUpdateTask();
  const { data: project } = useProject(task.project_id ?? "");

  // Editing states - use local state only while editing
  const [editingAssigned, setEditingAssigned] = useState(false);
  const [localAssignedValue, setLocalAssignedValue] = useState("");
  const assignedInputRef = useRef<HTMLInputElement>(null);

  const [editingTargetDate, setEditingTargetDate] = useState(false);
  const [localTargetDateValue, setLocalTargetDateValue] = useState("");
  const targetDateInputRef = useRef<HTMLInputElement>(null);

  // Display prop value when not editing, local value when editing
  const assignedValue = editingAssigned ? localAssignedValue : (task.assigned_to ?? "");
  const setAssignedValue = (value: string) => setLocalAssignedValue(value);

  const targetDateValue = editingTargetDate ? localTargetDateValue : formatDateForInput(task.target_date);
  const setTargetDateValue = (value: string) => setLocalTargetDateValue(value);

  // Start editing - copy current prop value to local state (resolved to slug)
  const startEditingAssigned = () => {
    setLocalAssignedValue(resolveToSlug(task.assigned_to));
    setEditingAssigned(true);
  };

  const startEditingTargetDate = () => {
    setLocalTargetDateValue(formatDateForInput(task.target_date));
    setEditingTargetDate(true);
  };

  // Focus inputs when editing starts
  useEffect(() => {
    if (editingAssigned && assignedInputRef.current) {
      assignedInputRef.current.focus();
      assignedInputRef.current.select();
    }
  }, [editingAssigned]);

  useEffect(() => {
    if (editingTargetDate && targetDateInputRef.current) {
      targetDateInputRef.current.focus();
    }
  }, [editingTargetDate]);

  const handlePriorityChange = async (value: string) => {
    try {
      await updateTask.mutateAsync({
        taskId: task.id,
        updates: { priority: parseInt(value, 10) },
      });
    } catch {
      toast.error("Failed to update priority");
    }
  };

  const handleComplexityChange = async (value: string) => {
    try {
      await updateTask.mutateAsync({
        taskId: task.id,
        updates: { estimated_complexity: value as Complexity },
      });
    } catch {
      toast.error("Failed to update complexity");
    }
  };

  const handleAssignedSave = async () => {
    const newValue = assignedValue.trim() || null;
    if (newValue === task.assigned_to) {
      setEditingAssigned(false);
      return;
    }

    try {
      await updateTask.mutateAsync({
        taskId: task.id,
        updates: { assigned_to: newValue },
      });
      setEditingAssigned(false);
    } catch {
      toast.error("Failed to update assignment");
      setAssignedValue(resolveToSlug(task.assigned_to));
    }
  };

  const handleAssignedKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      handleAssignedSave();
    } else if (e.key === "Escape") {
      setAssignedValue(resolveToSlug(task.assigned_to));
      setEditingAssigned(false);
    }
  };

  const handleTargetDateSave = async () => {
    const newValue = targetDateValue ? new Date(targetDateValue).toISOString() : null;
    if (newValue === task.target_date) {
      setEditingTargetDate(false);
      return;
    }

    try {
      await updateTask.mutateAsync({
        taskId: task.id,
        updates: { target_date: newValue },
      });
      setEditingTargetDate(false);
    } catch {
      toast.error("Failed to update target date");
      setTargetDateValue(formatDateForInput(task.target_date));
    }
  };

  const handleTargetDateKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      handleTargetDateSave();
    } else if (e.key === "Escape") {
      setTargetDateValue(formatDateForInput(task.target_date));
      setEditingTargetDate(false);
    }
  };

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-6">
      {/* Priority - Editable Select */}
      <Card>
        <CardContent className="pt-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">
            <AlertTriangle className="h-4 w-4" />
            Priority
          </div>
          <Select
            value={task.priority.toString()}
            onValueChange={handlePriorityChange}
            disabled={updateTask.isPending}
          >
            <SelectTrigger className={`w-full h-8 text-sm border-0 ${priorityColors[task.priority] ?? priorityColors[2]}`}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Object.entries(priorityLabels).map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  <span className={`px-2 py-0.5 rounded ${priorityColors[parseInt(value)]}`}>
                    {label}
                  </span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </CardContent>
      </Card>

      {/* Complexity - Editable Select */}
      <Card>
        <CardContent className="pt-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">
            <Target className="h-4 w-4" />
            Complexity
          </div>
          <Select
            value={task.estimated_complexity}
            onValueChange={handleComplexityChange}
            disabled={updateTask.isPending}
          >
            <SelectTrigger className="w-full h-8 text-sm border-0 bg-transparent hover:bg-muted/50">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Object.values(Complexity).map((complexity) => (
                <SelectItem key={complexity} value={complexity}>
                  {complexityLabels[complexity]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </CardContent>
      </Card>

      {/* Assigned To - Editable Input */}
      <Card>
        <CardContent className="pt-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">
            <User className="h-4 w-4" />
            Assigned To
          </div>
          {editingAssigned ? (
            <Input
              ref={assignedInputRef}
              value={assignedValue}
              onChange={(e) => setAssignedValue(e.target.value)}
              onKeyDown={handleAssignedKeyDown}
              onBlur={handleAssignedSave}
              placeholder="Agent ID"
              className="h-8 text-sm"
              disabled={updateTask.isPending}
            />
          ) : (
            <span
              className="font-medium cursor-pointer hover:bg-muted/50 px-2 py-1 -mx-2 rounded transition-colors inline-block"
              onClick={startEditingAssigned}
              title="Click to edit"
            >
              {getAgentDisplayName(task.assigned_to)}
            </span>
          )}
        </CardContent>
      </Card>

      {/* Created By - Read-only */}
      <Card>
        <CardContent className="pt-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">
            <User className="h-4 w-4" />
            Created By
          </div>
          <span className="font-medium">{getAgentDisplayName(task.created_by)}</span>
        </CardContent>
      </Card>

      {/* Sequence - Read-only */}
      {task.sequence != null && (
        <Card>
          <CardContent className="pt-4">
            <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">
              <Hash className="h-4 w-4" />
              Sequence
            </div>
            <span className="font-medium">#{task.sequence}</span>
          </CardContent>
        </Card>
      )}

      {/* Created At - Read-only */}
      <Card>
        <CardContent className="pt-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">
            <Calendar className="h-4 w-4" />
            Created
          </div>
          <span className="font-medium">{formatRelativeTime(task.created_at)}</span>
        </CardContent>
      </Card>

      {/* Started At - Read-only */}
      <Card>
        <CardContent className="pt-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">
            <Clock className="h-4 w-4" />
            Started
          </div>
          <span className="font-medium">{formatRelativeTime(task.started_at)}</span>
        </CardContent>
      </Card>

      {/* Target Date - Editable */}
      <Card>
        <CardContent className="pt-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">
            <Target className="h-4 w-4" />
            Target Date
          </div>
          {editingTargetDate ? (
            <Input
              ref={targetDateInputRef}
              type="datetime-local"
              value={targetDateValue}
              onChange={(e) => setTargetDateValue(e.target.value)}
              onKeyDown={handleTargetDateKeyDown}
              onBlur={handleTargetDateSave}
              className="h-8 text-sm"
              disabled={updateTask.isPending}
            />
          ) : (
            <span
              className="font-medium cursor-pointer hover:bg-muted/50 px-2 py-1 -mx-2 rounded transition-colors inline-block"
              onClick={startEditingTargetDate}
              title="Click to edit"
            >
              {task.target_date ? formatDate(task.target_date) : "Not set"}
            </span>
          )}
        </CardContent>
      </Card>

      {/* Completed At - Read-only */}
      <Card>
        <CardContent className="pt-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">
            <Clock className="h-4 w-4" />
            Completed
          </div>
          <span className="font-medium">{formatRelativeTime(task.completed_at)}</span>
        </CardContent>
      </Card>

      {/* Task Type - Read-only */}
      <Card>
        <CardContent className="pt-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">
            <Wrench className="h-4 w-4" />
            Task Type
          </div>
          <TaskTypeBadge type={task.task_type} />
        </CardContent>
      </Card>

      {/* Nature - Read-only */}
      <Card>
        <CardContent className="pt-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">
            <Briefcase className="h-4 w-4" />
            Nature
          </div>
          <Badge
            variant="outline"
            className={
              task.nature === TaskNature.TECHNICAL
                ? "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300"
                : "bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300"
            }
          >
            {task.nature === TaskNature.TECHNICAL ? "Technical" : "Non-Technical"}
          </Badge>
        </CardContent>
      </Card>

      {/* Branch Name - Read-only (all tasks follow git workflow) */}
      {task.branch_name && (
        <Card>
          <CardContent className="pt-4">
            <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">
              <GitBranch className="h-4 w-4" />
              Branch
            </div>
            <Badge variant="outline" className="font-mono text-xs">
              {task.branch_name}
            </Badge>
          </CardContent>
        </Card>
      )}

      {/* Pull Request - Read-only Link (only show when PR exists) */}
      {task.pr_number && (
        <Card>
          <CardContent className="pt-4">
            <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">
              <GitPullRequest className="h-4 w-4" />
              Pull Request
            </div>
            {task.pr_url ? (
              <a
                href={task.pr_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-blue-600 hover:underline dark:text-blue-400"
              >
                PR #{task.pr_number}
                <ExternalLink className="h-3 w-3" />
              </a>
            ) : (
              <span className="font-medium">#{task.pr_number}</span>
            )}
          </CardContent>
        </Card>
      )}

      {/* Project - Read-only Link */}
      <Card>
        <CardContent className="pt-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">
            <FolderGit2 className="h-4 w-4" />
            Project
          </div>
          {task.project_id && project ? (
            <Link
              href={`/projects`}
              className="font-medium text-blue-600 hover:underline dark:text-blue-400"
            >
              {project.name}
            </Link>
          ) : (
            <span className="font-medium text-muted-foreground">-</span>
          )}
        </CardContent>
      </Card>

      {/* Docs/PR Status - Read-only (only show for relevant statuses) */}
      {(task.docs_complete !== undefined || task.pr_created !== undefined) && (
        <Card className="col-span-2">
          <CardContent className="pt-4">
            <div className="flex items-center gap-2 text-sm text-muted-foreground mb-2">
              Completion Status
            </div>
            <DocsStatusBadge
              docsComplete={task.docs_complete}
              prCreated={task.pr_created}
              variant="full"
            />
          </CardContent>
        </Card>
      )}
    </div>
  );
}
