"use client";

import { useState } from "react";
import { Task } from "@/types";
import { useUpdateTask } from "@/hooks/use-tasks";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Markdown } from "@/components/ui/markdown";
import {
  FileText,
  Code,
  TestTube,
  Shield,
  GitPullRequest,
  BookText,
  Edit3,
  Eye,
  Check,
  X,
  Plus,
} from "lucide-react";
import { toast } from "sonner";

interface TabNotesProps {
  task: Task;
}

type NoteField =
  | "quick_context"
  | "dev_notes"
  | "qa_notes"
  | "auditor_notes"
  | "pr_reviewer_notes"
  | "doc_notes";

// The PR reviewer's verdict pill, read from the structured source of truth.
function prReviewBadge(task: Task): React.ReactNode {
  const verdict = (
    task.notes_structured as
      | { pr_review?: { verdict?: string } }
      | null
      | undefined
  )?.pr_review?.verdict;
  if (!verdict) {
    return (
      <Badge variant="outline" className="ml-2 text-teal-600 border-teal-300">
        Review Gate
      </Badge>
    );
  }
  const map: Record<string, { label: string; cls: string }> = {
    approved: { label: "Approved", cls: "bg-green-500" },
    passed: { label: "Passed", cls: "bg-green-500" },
    changes_requested: { label: "Changes Requested", cls: "bg-amber-500" },
    failed: { label: "Failed", cls: "bg-red-500" },
  };
  const v = map[verdict] ?? { label: verdict, cls: "bg-gray-500" };
  return <Badge className={`ml-2 ${v.cls} text-white`}>{v.label}</Badge>;
}

// The card background mirrors the PR reviewer's verdict, so a FAILED review reads
// as red — not the neutral teal that made a failure look green/passing at a glance.
function prReviewCardBg(task: Task): string {
  const verdict = (
    task.notes_structured as
      | { pr_review?: { verdict?: string } }
      | null
      | undefined
  )?.pr_review?.verdict;
  const map: Record<string, string> = {
    approved:
      "bg-green-50 dark:bg-green-950 border border-green-200 dark:border-green-800",
    passed:
      "bg-green-50 dark:bg-green-950 border border-green-200 dark:border-green-800",
    changes_requested:
      "bg-amber-50 dark:bg-amber-950 border border-amber-200 dark:border-amber-800",
    failed: "bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800",
  };
  return (
    (verdict ? map[verdict] : undefined) ??
    "bg-teal-50 dark:bg-teal-950 border border-teal-200 dark:border-teal-800"
  );
}

interface NoteCardProps {
  task: Task;
  field: NoteField;
  title: string;
  icon: React.ReactNode;
  badge?: React.ReactNode;
  bgClass?: string;
}

function EditableNoteCard({
  task,
  field,
  title,
  icon,
  badge,
  bgClass,
}: NoteCardProps) {
  const updateTask = useUpdateTask();
  const [isEditing, setIsEditing] = useState(false);
  const [localEditValue, setLocalEditValue] = useState("");
  const [editMode, setEditMode] = useState<"write" | "preview">("write");

  const currentValue = task[field];

  // Display prop value when not editing, local value when editing
  const editValue = isEditing ? localEditValue : (currentValue ?? "");
  const setEditValue = (value: string) => setLocalEditValue(value);

  // Start editing - copy current prop value to local state
  const startEditing = () => {
    setLocalEditValue(currentValue ?? "");
    setIsEditing(true);
  };

  const handleSave = async () => {
    const newValue = editValue.trim() || null;
    if (newValue === currentValue) {
      setIsEditing(false);
      setEditMode("write");
      return;
    }

    try {
      await updateTask.mutateAsync({
        taskId: task.id,
        updates: { [field]: newValue },
      });
      setIsEditing(false);
      setEditMode("write");
    } catch {
      toast.error(`Failed to update ${title.toLowerCase()}`);
    }
  };

  const handleCancel = () => {
    setEditValue(currentValue ?? "");
    setIsEditing(false);
    setEditMode("write");
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      handleCancel();
    }
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      handleSave();
    }
  };

  // If no content and not editing, show placeholder
  if (!currentValue && !isEditing) {
    return (
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="text-lg flex items-center gap-2">
              {icon}
              {title}
              {badge}
            </CardTitle>
            <Button size="sm" variant="ghost" onClick={startEditing}>
              <Plus className="h-4 w-4 mr-1" />
              Add
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          <p
            className="text-muted-foreground italic cursor-pointer hover:bg-muted/30 rounded-md p-2 -m-2 transition-colors"
            onClick={startEditing}
          >
            No {title.toLowerCase()} added yet. Click to add.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg flex items-center gap-2">
            {icon}
            {title}
            {badge}
          </CardTitle>
          {isEditing ? (
            <div className="flex items-center gap-2">
              <Tabs
                value={editMode}
                onValueChange={(v) => setEditMode(v as "write" | "preview")}
              >
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
            <Button size="sm" variant="ghost" onClick={startEditing}>
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
                placeholder={`Add ${title.toLowerCase()}...`}
                className="min-h-[150px] font-mono text-sm"
                disabled={updateTask.isPending}
                autoFocus
              />
            ) : (
              <div
                className={`min-h-[150px] p-4 rounded-lg ${bgClass ?? "bg-muted/50"}`}
              >
                {editValue ? (
                  <Markdown className="text-sm">{editValue}</Markdown>
                ) : (
                  <p className="text-muted-foreground text-sm italic">
                    Nothing to preview
                  </p>
                )}
              </div>
            )}
            <p className="text-xs text-muted-foreground">
              Markdown supported. Press Ctrl/Cmd + Enter to save, Escape to
              cancel.
            </p>
          </div>
        ) : (
          <div
            className={`rounded-lg p-4 cursor-pointer hover:opacity-80 transition-opacity ${bgClass ?? "bg-muted/50"}`}
            onClick={startEditing}
            title="Click to edit"
          >
            <Markdown className="text-sm">{currentValue!}</Markdown>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function TabNotes({ task }: TabNotesProps) {
  // Show all note sections, even empty ones (they can be added)
  return (
    <div className="space-y-6">
      {/* Quick Context */}
      <EditableNoteCard
        task={task}
        field="quick_context"
        title="Quick Context"
        icon={<FileText className="h-5 w-5" />}
        badge={
          <Badge variant="outline" className="ml-2">
            For Resumption
          </Badge>
        }
        bgClass="bg-blue-50 dark:bg-blue-950 border border-blue-200 dark:border-blue-800"
      />

      {/* Dev Notes */}
      <EditableNoteCard
        task={task}
        field="dev_notes"
        title="Developer Notes"
        icon={<Code className="h-5 w-5" />}
        bgClass="bg-muted/50"
      />

      {/* Documenter Notes */}
      <EditableNoteCard
        task={task}
        field="doc_notes"
        title="Documenter Notes"
        icon={<BookText className="h-5 w-5" />}
        bgClass="bg-muted/50"
      />

      {/* QA Notes */}
      <EditableNoteCard
        task={task}
        field="qa_notes"
        title="QA Notes"
        icon={<TestTube className="h-5 w-5" />}
        badge={
          <Badge
            variant={
              task.qa_verified === true
                ? "default"
                : task.qa_verified === false
                  ? "destructive"
                  : "secondary"
            }
            className="ml-2"
          >
            {task.qa_verified === true
              ? "Passed"
              : task.qa_verified === false
                ? "Failed"
                : "Pending"}
          </Badge>
        }
        bgClass={
          task.qa_verified === true
            ? "bg-green-50 dark:bg-green-950 border border-green-200 dark:border-green-800"
            : task.qa_verified === false
              ? "bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800"
              : "bg-muted/50"
        }
      />

      {/* PR Reviewer Notes */}
      <EditableNoteCard
        task={task}
        field="pr_reviewer_notes"
        title="PR Reviewer Notes"
        icon={<GitPullRequest className="h-5 w-5" />}
        badge={prReviewBadge(task)}
        bgClass={prReviewCardBg(task)}
      />

      {/* Auditor Notes */}
      <EditableNoteCard
        task={task}
        field="auditor_notes"
        title="Auditor Notes"
        icon={<Shield className="h-5 w-5" />}
        badge={
          <Badge
            variant="outline"
            className="ml-2 text-purple-600 border-purple-300"
          >
            Confidential
          </Badge>
        }
        bgClass="bg-purple-50 dark:bg-purple-950 border border-purple-200 dark:border-purple-800"
      />
    </div>
  );
}
