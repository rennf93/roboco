"use client";

import { useState, useRef, useEffect } from "react";
import { Task } from "@/types";
import { useUpdateTask } from "@/hooks/use-tasks";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { Plus, Trash2, Edit3, Check, X } from "lucide-react";
import { toast } from "sonner";

interface AcceptanceCriteriaProps {
  task: Task;
}

export function AcceptanceCriteria({ task }: AcceptanceCriteriaProps) {
  const updateTask = useUpdateTask();
  const criteria = task.acceptance_criteria ?? [];

  // Add new criterion state
  const [isAdding, setIsAdding] = useState(false);
  const [newCriterion, setNewCriterion] = useState("");
  const newInputRef = useRef<HTMLInputElement>(null);

  // Edit existing criterion state
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [editValue, setEditValue] = useState("");
  const editInputRef = useRef<HTMLInputElement>(null);

  // Focus new input when adding starts
  useEffect(() => {
    if (isAdding && newInputRef.current) {
      newInputRef.current.focus();
    }
  }, [isAdding]);

  // Focus edit input when editing starts
  useEffect(() => {
    if (editingIndex !== null && editInputRef.current) {
      editInputRef.current.focus();
      editInputRef.current.select();
    }
  }, [editingIndex]);

  // Parse criterion to get text and completion status
  const parseCriterion = (criterion: string): { text: string; completed: boolean } => {
    if (criterion.startsWith("[x]") || criterion.startsWith("[X]")) {
      return { text: criterion.slice(3).trim(), completed: true };
    }
    if (criterion.startsWith("[ ]")) {
      return { text: criterion.slice(3).trim(), completed: false };
    }
    // No checkbox notation - treat as uncompleted
    return { text: criterion, completed: false };
  };

  // Format criterion with checkbox notation
  const formatCriterion = (text: string, completed: boolean): string => {
    return completed ? `[x] ${text}` : `[ ] ${text}`;
  };

  // Count completed criteria
  const completedCount = criteria.filter(
    (c) => c.startsWith("[x]") || c.startsWith("[X]")
  ).length;

  // Toggle criterion completion
  const toggleCriterion = async (index: number) => {
    const newCriteria = [...criteria];
    const current = newCriteria[index];
    const { text, completed } = parseCriterion(current);
    newCriteria[index] = formatCriterion(text, !completed);

    try {
      await updateTask.mutateAsync({
        taskId: task.id,
        updates: { acceptance_criteria: newCriteria },
      });
    } catch {
      toast.error("Failed to update acceptance criteria");
    }
  };

  // Add new criterion
  const handleAddCriterion = async () => {
    if (!newCriterion.trim()) {
      setIsAdding(false);
      return;
    }

    const newCriteria = [...criteria, formatCriterion(newCriterion.trim(), false)];

    try {
      await updateTask.mutateAsync({
        taskId: task.id,
        updates: { acceptance_criteria: newCriteria },
      });
      setNewCriterion("");
      // Keep adding mode open for quick entry
    } catch {
      toast.error("Failed to add criterion");
    }
  };

  const handleAddKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      handleAddCriterion();
    } else if (e.key === "Escape") {
      setNewCriterion("");
      setIsAdding(false);
    }
  };

  // Edit criterion
  const startEditing = (index: number) => {
    const { text } = parseCriterion(criteria[index]);
    setEditingIndex(index);
    setEditValue(text);
  };

  const handleSaveEdit = async () => {
    if (editingIndex === null) return;

    const { completed } = parseCriterion(criteria[editingIndex]);
    const newText = editValue.trim();

    if (!newText) {
      // If empty, delete the criterion
      await handleDeleteCriterion(editingIndex);
      setEditingIndex(null);
      return;
    }

    const newCriteria = [...criteria];
    newCriteria[editingIndex] = formatCriterion(newText, completed);

    try {
      await updateTask.mutateAsync({
        taskId: task.id,
        updates: { acceptance_criteria: newCriteria },
      });
      setEditingIndex(null);
    } catch {
      toast.error("Failed to update criterion");
    }
  };

  const handleEditKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      handleSaveEdit();
    } else if (e.key === "Escape") {
      setEditingIndex(null);
    }
  };

  // Delete criterion
  const handleDeleteCriterion = async (index: number) => {
    const newCriteria = criteria.filter((_, i) => i !== index);

    try {
      await updateTask.mutateAsync({
        taskId: task.id,
        updates: { acceptance_criteria: newCriteria },
      });
    } catch {
      toast.error("Failed to delete criterion");
    }
  };

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg">Acceptance Criteria</CardTitle>
          <div className="flex items-center gap-2">
            <span className="text-sm text-muted-foreground">
              {completedCount}/{criteria.length} completed
            </span>
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
        </div>
      </CardHeader>
      <CardContent>
        {criteria.length === 0 && !isAdding ? (
          <p
            className="text-muted-foreground italic cursor-pointer hover:bg-muted/30 rounded-md p-2 -m-2 transition-colors"
            onClick={() => setIsAdding(true)}
          >
            No acceptance criteria defined. Click to add one.
          </p>
        ) : (
          <ul className="space-y-2">
            {criteria.map((criterion, idx) => {
              const { text, completed } = parseCriterion(criterion);
              const isEditingThis = editingIndex === idx;

              return (
                <li key={idx} className="flex items-center gap-2 group">
                  <Checkbox
                    checked={completed}
                    onCheckedChange={() => toggleCriterion(idx)}
                    disabled={updateTask.isPending || isEditingThis}
                    className="shrink-0"
                  />

                  {isEditingThis ? (
                    <div className="flex-1 flex items-center gap-2">
                      <Input
                        ref={editInputRef}
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        onKeyDown={handleEditKeyDown}
                        onBlur={handleSaveEdit}
                        className="h-8 text-sm flex-1"
                        disabled={updateTask.isPending}
                      />
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setEditingIndex(null)}
                        className="h-7 w-7 p-0"
                      >
                        <X className="h-4 w-4" />
                      </Button>
                      <Button
                        size="sm"
                        onClick={handleSaveEdit}
                        className="h-7 w-7 p-0"
                      >
                        <Check className="h-4 w-4" />
                      </Button>
                    </div>
                  ) : (
                    <>
                      <span
                        className={`flex-1 cursor-pointer select-none hover:bg-muted/30 px-2 py-1 -mx-2 rounded transition-colors ${
                          completed ? "line-through text-muted-foreground" : ""
                        }`}
                        onClick={() => startEditing(idx)}
                        title="Click to edit"
                      >
                        {text}
                      </span>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => startEditing(idx)}
                        className="h-7 w-7 p-0 opacity-0 group-hover:opacity-100 transition-opacity"
                      >
                        <Edit3 className="h-3 w-3" />
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => handleDeleteCriterion(idx)}
                        className="h-7 w-7 p-0 opacity-0 group-hover:opacity-100 transition-opacity text-destructive hover:text-destructive"
                      >
                        <Trash2 className="h-3 w-3" />
                      </Button>
                    </>
                  )}
                </li>
              );
            })}

            {/* Add new criterion input */}
            {isAdding && (
              <li className="flex items-center gap-2">
                <Checkbox checked={false} disabled className="shrink-0" />
                <Input
                  ref={newInputRef}
                  value={newCriterion}
                  onChange={(e) => setNewCriterion(e.target.value)}
                  onKeyDown={handleAddKeyDown}
                  placeholder="Add criterion..."
                  className="h-8 text-sm flex-1"
                  disabled={updateTask.isPending}
                />
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setNewCriterion("");
                    setIsAdding(false);
                  }}
                  className="h-7 w-7 p-0"
                >
                  <X className="h-4 w-4" />
                </Button>
                <Button
                  size="sm"
                  onClick={handleAddCriterion}
                  disabled={!newCriterion.trim() || updateTask.isPending}
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
