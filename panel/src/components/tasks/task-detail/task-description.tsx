"use client";

import { useState } from "react";
import { Task } from "@/types";
import { useUpdateTask } from "@/hooks/use-tasks";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Markdown } from "@/components/ui/markdown";
import { Edit3, Eye, Check, X } from "lucide-react";
import { toast } from "sonner";

interface TaskDescriptionProps {
  task: Task;
}

export function TaskDescription({ task }: TaskDescriptionProps) {
  const updateTask = useUpdateTask();
  const [isEditing, setIsEditing] = useState(false);
  const [localEditValue, setLocalEditValue] = useState("");
  const [editMode, setEditMode] = useState<"write" | "preview">("write");

  // Display prop value when not editing, local value when editing
  const editValue = isEditing ? localEditValue : task.description;
  const setEditValue = (value: string) => setLocalEditValue(value);

  // Start editing - copy current prop value to local state
  const startEditing = () => {
    setLocalEditValue(task.description);
    setIsEditing(true);
  };

  const handleCheckboxChange = async (newDescription: string) => {
    try {
      await updateTask.mutateAsync({
        taskId: task.id,
        updates: { description: newDescription },
      });
    } catch {
      toast.error("Failed to update task");
    }
  };

  const handleSave = async () => {
    if (editValue === task.description) {
      setIsEditing(false);
      return;
    }

    try {
      await updateTask.mutateAsync({
        taskId: task.id,
        updates: { description: editValue },
      });
      setIsEditing(false);
      setEditMode("write");
    } catch {
      toast.error("Failed to update description");
    }
  };

  const handleCancel = () => {
    setEditValue(task.description);
    setIsEditing(false);
    setEditMode("write");
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      handleCancel();
    }
    // Save with Cmd/Ctrl + Enter
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      handleSave();
    }
  };

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg">Description</CardTitle>
          {isEditing ? (
            <div className="flex items-center gap-2">
              <Tabs value={editMode} onValueChange={(v) => setEditMode(v as "write" | "preview")}>
                <TabsList className="h-8">
                  <TabsTrigger value="write" className="text-xs px-2 h-6">
                    <Edit3 className="h-3 w-3 mr-1" />
                    Write
                  </TabsTrigger>
                  <TabsTrigger value="preview" className="text-xs px-2 h-6">
                    <Eye className="h-3 w-3 mr-1" />
                    Preview
                  </TabsTrigger>
                </TabsList>
              </Tabs>
              <Button
                size="sm"
                variant="ghost"
                onClick={handleCancel}
                disabled={updateTask.isPending}
              >
                <X className="h-4 w-4" />
              </Button>
              <Button
                size="sm"
                onClick={handleSave}
                disabled={updateTask.isPending}
              >
                <Check className="h-4 w-4 mr-1" />
                Save
              </Button>
            </div>
          ) : (
            <Button
              size="sm"
              variant="ghost"
              onClick={startEditing}
            >
              <Edit3 className="h-4 w-4 mr-1" />
              Edit
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent>
        {isEditing ? (
          <div className="space-y-2">
            {editMode === "write" ? (
              <Textarea
                value={editValue}
                onChange={(e) => setEditValue(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Add a description..."
                className="min-h-[200px] font-mono text-sm"
                disabled={updateTask.isPending}
                autoFocus
              />
            ) : (
              <div className="min-h-[200px] p-3 border rounded-md bg-muted/30">
                {editValue ? (
                  <Markdown>{editValue}</Markdown>
                ) : (
                  <p className="text-muted-foreground text-sm italic">Nothing to preview</p>
                )}
              </div>
            )}
            <p className="text-xs text-muted-foreground">
              Markdown supported. Press Ctrl/Cmd + Enter to save, Escape to cancel.
            </p>
          </div>
        ) : task.description ? (
          <div
            className="cursor-pointer hover:bg-muted/30 rounded-md p-2 -m-2 transition-colors"
            onClick={startEditing}
            title="Click to edit"
          >
            <Markdown
              onCheckboxChange={handleCheckboxChange}
              disabled={updateTask.isPending}
            >
              {task.description}
            </Markdown>
          </div>
        ) : (
          <p
            className="text-muted-foreground italic cursor-pointer hover:bg-muted/30 rounded-md p-2 -m-2 transition-colors"
            onClick={startEditing}
            title="Click to add description"
          >
            No description provided. Click to add one.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
