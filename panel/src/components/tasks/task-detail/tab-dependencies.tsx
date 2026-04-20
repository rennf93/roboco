"use client";

import { useState, useRef, useEffect } from "react";
import { Task } from "@/types";
import { useUpdateTask } from "@/hooks/use-tasks";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { ArrowUp, Link2, AlertTriangle, Plus, Trash2, X, Check } from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";

interface TabDependenciesProps {
  task: Task;
}

interface DependencyListProps {
  task: Task;
  field: "dependency_ids" | "blocker_ids";
  title: string;
  icon: React.ReactNode;
  emptyMessage: string;
  itemBgClass?: string;
  itemBorderClass?: string;
  badgeLabel?: string;
  badgeClass?: string;
}

function DependencyList({
  task,
  field,
  title,
  icon,
  emptyMessage,
  itemBgClass = "",
  itemBorderClass = "border",
  badgeLabel = "View",
  badgeClass = "",
}: DependencyListProps) {
  const updateTask = useUpdateTask();
  const ids = task[field];

  const [isAdding, setIsAdding] = useState(false);
  const [newId, setNewId] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isAdding && inputRef.current) {
      inputRef.current.focus();
    }
  }, [isAdding]);

  const handleAdd = async () => {
    const trimmedId = newId.trim();
    if (!trimmedId) {
      setIsAdding(false);
      return;
    }

    // Don't add duplicates or self-reference
    if (ids.includes(trimmedId)) {
      toast.error("This task is already in the list");
      return;
    }
    if (trimmedId === task.id) {
      toast.error("A task cannot depend on itself");
      return;
    }

    const newIds = [...ids, trimmedId];

    try {
      await updateTask.mutateAsync({
        taskId: task.id,
        updates: { [field]: newIds },
      });
      setNewId("");
      // Keep adding mode open for quick entry
    } catch {
      toast.error(`Failed to add ${field === "dependency_ids" ? "dependency" : "blocker"}`);
    }
  };

  const handleRemove = async (idToRemove: string) => {
    const newIds = ids.filter((id) => id !== idToRemove);

    try {
      await updateTask.mutateAsync({
        taskId: task.id,
        updates: { [field]: newIds },
      });
    } catch {
      toast.error(`Failed to remove ${field === "dependency_ids" ? "dependency" : "blocker"}`);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      handleAdd();
    } else if (e.key === "Escape") {
      setNewId("");
      setIsAdding(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg flex items-center gap-2">
            {icon}
            {title}
            {ids.length > 0 && field === "blocker_ids" && (
              <Badge variant="destructive" className="ml-2">
                {ids.length}
              </Badge>
            )}
          </CardTitle>
          {!isAdding && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setIsAdding(true)}
            >
              <Plus className="h-4 w-4 mr-1" />
              Add
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent>
        {ids.length === 0 && !isAdding ? (
          <p
            className="text-muted-foreground italic cursor-pointer hover:bg-muted/30 rounded-md p-2 -m-2 transition-colors"
            onClick={() => setIsAdding(true)}
          >
            {emptyMessage}
          </p>
        ) : (
          <ul className="space-y-2">
            {ids.map((depId) => (
              <li key={depId} className="group">
                <div className={`flex items-center gap-2 p-3 rounded-lg ${itemBorderClass} ${itemBgClass} transition-colors`}>
                  <Link2 className="h-4 w-4 text-muted-foreground shrink-0" />
                  <Link href={`/tasks/${depId}`} className="flex-1">
                    <span className="font-mono text-sm hover:underline">{depId.slice(0, 8)}...</span>
                  </Link>
                  <Badge variant="outline" className={badgeClass}>
                    {badgeLabel}
                  </Badge>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => handleRemove(depId)}
                    className="h-7 w-7 p-0 opacity-0 group-hover:opacity-100 transition-opacity text-destructive hover:text-destructive"
                    disabled={updateTask.isPending}
                  >
                    <Trash2 className="h-3 w-3" />
                  </Button>
                </div>
              </li>
            ))}

            {/* Add new input */}
            {isAdding && (
              <li className="flex items-center gap-2">
                <Link2 className="h-4 w-4 text-muted-foreground shrink-0" />
                <Input
                  ref={inputRef}
                  value={newId}
                  onChange={(e) => setNewId(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder="Enter task ID..."
                  className="h-8 text-sm flex-1 font-mono"
                  disabled={updateTask.isPending}
                />
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setNewId("");
                    setIsAdding(false);
                  }}
                  className="h-7 w-7 p-0"
                >
                  <X className="h-4 w-4" />
                </Button>
                <Button
                  size="sm"
                  onClick={handleAdd}
                  disabled={!newId.trim() || updateTask.isPending}
                  className="h-7 w-7 p-0"
                >
                  <Check className="h-4 w-4" />
                </Button>
              </li>
            )}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

export function TabDependencies({ task }: TabDependenciesProps) {
  const updateTask = useUpdateTask();

  // Parent task editing - use local state only while editing
  const [editingParent, setEditingParent] = useState(false);
  const [localParentValue, setLocalParentValue] = useState("");
  const parentInputRef = useRef<HTMLInputElement>(null);

  // Display prop value when not editing, local value when editing
  const parentValue = editingParent ? localParentValue : (task.parent_task_id ?? "");
  const setParentValue = (value: string) => setLocalParentValue(value);

  // Start editing - copy current prop value to local state
  const startEditingParent = () => {
    setLocalParentValue(task.parent_task_id ?? "");
    setEditingParent(true);
  };

  // Focus input when editing starts
  useEffect(() => {
    if (editingParent && parentInputRef.current) {
      parentInputRef.current.focus();
      parentInputRef.current.select();
    }
  }, [editingParent]);

  const handleParentSave = async () => {
    const newValue = parentValue.trim() || null;
    if (newValue === task.parent_task_id) {
      setEditingParent(false);
      return;
    }

    // Prevent self-reference
    if (newValue === task.id) {
      toast.error("A task cannot be its own parent");
      setParentValue(task.parent_task_id ?? "");
      return;
    }

    try {
      await updateTask.mutateAsync({
        taskId: task.id,
        updates: { parent_task_id: newValue },
      });
      setEditingParent(false);
    } catch {
      toast.error("Failed to update parent task");
      setParentValue(task.parent_task_id ?? "");
    }
  };

  const handleParentKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      handleParentSave();
    } else if (e.key === "Escape") {
      setParentValue(task.parent_task_id ?? "");
      setEditingParent(false);
    }
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      {/* Dependencies (upstream - blocks this task) */}
      <DependencyList
        task={task}
        field="dependency_ids"
        title="Dependencies"
        icon={<ArrowUp className="h-5 w-5 text-blue-500" />}
        emptyMessage="No upstream dependencies. Click to add one."
        itemBgClass="hover:bg-muted/50"
      />

      {/* Blockers (current blockers) */}
      <DependencyList
        task={task}
        field="blocker_ids"
        title="Current Blockers"
        icon={<AlertTriangle className="h-5 w-5 text-red-500" />}
        emptyMessage="No active blockers. Click to add one."
        itemBgClass="bg-red-50 hover:bg-red-100 dark:bg-red-950 dark:hover:bg-red-900"
        itemBorderClass="border border-red-200 dark:border-red-800"
        badgeLabel="Blocking"
        badgeClass="border-red-300 text-red-600"
      />

      {/* Parent Task */}
      <Card className="lg:col-span-2">
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="text-lg flex items-center gap-2">
              <ArrowUp className="h-5 w-5 text-purple-500" />
              Parent Task
            </CardTitle>
          </div>
        </CardHeader>
        <CardContent>
          {editingParent ? (
            <div className="flex items-center gap-2">
              <Link2 className="h-4 w-4 text-muted-foreground shrink-0" />
              <Input
                ref={parentInputRef}
                value={parentValue}
                onChange={(e) => setParentValue(e.target.value)}
                onKeyDown={handleParentKeyDown}
                onBlur={handleParentSave}
                placeholder="Enter parent task ID..."
                className="h-8 text-sm flex-1 font-mono"
                disabled={updateTask.isPending}
              />
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setParentValue(task.parent_task_id ?? "");
                  setEditingParent(false);
                }}
                className="h-7 w-7 p-0"
              >
                <X className="h-4 w-4" />
              </Button>
              <Button
                size="sm"
                onClick={handleParentSave}
                disabled={updateTask.isPending}
                className="h-7 w-7 p-0"
              >
                <Check className="h-4 w-4" />
              </Button>
            </div>
          ) : task.parent_task_id ? (
            <div
              className="flex items-center gap-2 p-3 rounded-lg border hover:bg-muted/50 transition-colors cursor-pointer group"
              onClick={startEditingParent}
              title="Click to edit"
            >
              <Link2 className="h-4 w-4 text-muted-foreground" />
              <Link href={`/tasks/${task.parent_task_id}`} className="flex-1" onClick={(e) => e.stopPropagation()}>
                <span className="font-mono text-sm hover:underline">{task.parent_task_id.slice(0, 8)}...</span>
              </Link>
              <Badge variant="outline" className="ml-auto">
                View Parent
              </Badge>
              <Button
                size="sm"
                variant="ghost"
                onClick={(e) => {
                  e.stopPropagation();
                  startEditingParent();
                }}
                className="h-7 w-7 p-0 opacity-0 group-hover:opacity-100 transition-opacity"
              >
                <Trash2 className="h-3 w-3 text-destructive" />
              </Button>
            </div>
          ) : (
            <p
              className="text-muted-foreground italic cursor-pointer hover:bg-muted/30 rounded-md p-2 -m-2 transition-colors"
              onClick={startEditingParent}
            >
              No parent task. Click to set one.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
