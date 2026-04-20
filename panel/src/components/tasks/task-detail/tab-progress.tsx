"use client";

import { useState, useRef, useEffect } from "react";
import { Task, ProgressUpdate, Checkpoint } from "@/types";
import { useUpdateTask } from "@/hooks/use-tasks";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Progress } from "@/components/ui/progress";
import {
  Clock,
  Bookmark,
  Plus,
  Trash2,
  X,
  Check,
  MessageSquare,
  User,
  ListTodo,
  FileText,
} from "lucide-react";
import { toast } from "sonner";
import { getAgentDisplayName } from "@/lib/agent-utils";

interface TabProgressProps {
  task: Task;
}

// Generate a simple unique ID
function generateId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;
}

function formatTime(timestamp: string): string {
  const date = new Date(timestamp);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / (1000 * 60));
  const diffHours = Math.floor(diffMins / 60);
  const diffDays = Math.floor(diffHours / 24);

  if (diffMins < 1) return "Just now";
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// ============================================================================
// Progress Updates Section
// ============================================================================
function ProgressUpdatesSection({ task }: { task: Task }) {
  const updateTask = useUpdateTask();
  const updates = task.progress_updates;

  const [isAdding, setIsAdding] = useState(false);
  const [newMessage, setNewMessage] = useState("");
  const [newPercentage, setNewPercentage] = useState<string>("");

  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (isAdding && inputRef.current) inputRef.current.focus();
  }, [isAdding]);

  // Sort by most recent first
  const sortedUpdates = [...updates].sort(
    (a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime()
  );

  const latestWithPercentage = sortedUpdates.find((u) => u.percentage !== null);
  const currentProgress = latestWithPercentage?.percentage ?? 0;

  const handleAdd = async () => {
    if (!newMessage.trim()) {
      setIsAdding(false);
      return;
    }

    const newUpdate: ProgressUpdate = {
      timestamp: new Date().toISOString(),
      agent_id: "CEO",
      message: newMessage.trim(),
      percentage: newPercentage ? parseInt(newPercentage, 10) : null,
    };

    try {
      await updateTask.mutateAsync({
        taskId: task.id,
        updates: { progress_updates: [...updates, newUpdate] },
      });
      setNewMessage("");
      setNewPercentage("");
      setIsAdding(false);
    } catch {
      toast.error("Failed to add progress update");
    }
  };

  const handleDelete = async (idx: number) => {
    // Find the actual index in unsorted array
    const update = sortedUpdates[idx];
    const actualIdx = updates.findIndex(
      (u) => u.timestamp === update.timestamp && u.message === update.message
    );
    if (actualIdx === -1) return;

    const newUpdates = updates.filter((_, i) => i !== actualIdx);

    try {
      await updateTask.mutateAsync({
        taskId: task.id,
        updates: { progress_updates: newUpdates },
      });
    } catch {
      toast.error("Failed to delete progress update");
    }
  };

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg">Progress Updates</CardTitle>
          <div className="flex items-center gap-2">
            <span className="text-sm text-muted-foreground">
              {updates.length} update{updates.length !== 1 ? "s" : ""}
            </span>
            {!isAdding && (
              <Button size="sm" variant="ghost" onClick={() => setIsAdding(true)}>
                <Plus className="h-4 w-4 mr-1" />Add
              </Button>
            )}
          </div>
        </div>
        {latestWithPercentage && (
          <div className="mt-2">
            <div className="flex items-center justify-between text-sm mb-1">
              <span className="text-muted-foreground">Overall Progress</span>
              <span className="font-medium">{currentProgress}%</span>
            </div>
            <Progress value={currentProgress} className="h-2" />
          </div>
        )}
      </CardHeader>
      <CardContent>
        {/* Add new update form */}
        {isAdding && (
          <div className="border rounded-lg p-4 mb-4 space-y-3">
            <Textarea
              ref={inputRef}
              value={newMessage}
              onChange={(e) => setNewMessage(e.target.value)}
              placeholder="What progress have you made?"
              className="text-sm min-h-[80px]"
            />
            <div className="flex items-center gap-2">
              <Input
                type="number"
                value={newPercentage}
                onChange={(e) => setNewPercentage(e.target.value)}
                placeholder="Progress %"
                className="w-32 h-8 text-sm"
                min="0"
                max="100"
              />
              <div className="flex-1" />
              <Button size="sm" variant="ghost" onClick={() => { setNewMessage(""); setNewPercentage(""); setIsAdding(false); }}>
                <X className="h-4 w-4" />
              </Button>
              <Button size="sm" onClick={handleAdd} disabled={!newMessage.trim() || updateTask.isPending}>
                <Check className="h-4 w-4 mr-1" />Add Update
              </Button>
            </div>
          </div>
        )}

        {sortedUpdates.length === 0 ? (
          <p
            className="text-muted-foreground italic cursor-pointer hover:bg-muted/30 p-2 rounded"
            onClick={() => setIsAdding(true)}
          >
            No progress updates yet. Click to add one.
          </p>
        ) : (
          <div className="relative">
            {/* Timeline line */}
            <div className="absolute left-3 top-0 bottom-0 w-0.5 bg-border" />

            <ul className="space-y-4">
              {sortedUpdates.map((update, idx) => (
                <li key={idx} className="relative pl-8 group">
                  {/* Timeline dot */}
                  <div className="absolute left-0 top-1.5 w-6 h-6 rounded-full bg-background border-2 border-primary flex items-center justify-center">
                    <MessageSquare className="h-3 w-3 text-primary" />
                  </div>

                  <div className="bg-muted/50 rounded-lg p-3">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-sm font-medium">
                        {getAgentDisplayName(update.agent_id)}
                      </span>
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-muted-foreground flex items-center gap-1">
                          <Clock className="h-3 w-3" />
                          {formatTime(update.timestamp)}
                        </span>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => handleDelete(idx)}
                          className="h-6 w-6 p-0 opacity-0 group-hover:opacity-100 text-destructive"
                        >
                          <Trash2 className="h-3 w-3" />
                        </Button>
                      </div>
                    </div>
                    <p className="text-sm">{update.message}</p>
                    {update.percentage !== null && (
                      <div className="mt-2">
                        <div className="flex items-center gap-2">
                          <Progress value={update.percentage} className="h-1.5 flex-1" />
                          <span className="text-xs text-muted-foreground w-10 text-right">
                            {update.percentage}%
                          </span>
                        </div>
                      </div>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ============================================================================
// Checkpoints Section
// ============================================================================
function CheckpointsSection({ task }: { task: Task }) {
  const updateTask = useUpdateTask();
  const checkpoints = task.checkpoints;

  const [isAdding, setIsAdding] = useState(false);
  const [newSummary, setNewSummary] = useState("");
  const [newRemaining, setNewRemaining] = useState("");
  const [newNotes, setNewNotes] = useState("");

  const summaryRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (isAdding && summaryRef.current) summaryRef.current.focus();
  }, [isAdding]);

  // Sort by timestamp (newest first)
  const sortedCheckpoints = [...checkpoints].sort(
    (a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime()
  );

  const handleAdd = async () => {
    if (!newSummary.trim()) {
      setIsAdding(false);
      return;
    }

    const newCheckpoint: Checkpoint = {
      id: generateId(),
      timestamp: new Date().toISOString(),
      agent_id: "CEO",
      state_summary: newSummary.trim(),
      remaining_work: newRemaining
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean),
      notes: newNotes.trim() || null,
    };

    try {
      await updateTask.mutateAsync({
        taskId: task.id,
        updates: { checkpoints: [...checkpoints, newCheckpoint] },
      });
      setNewSummary("");
      setNewRemaining("");
      setNewNotes("");
      setIsAdding(false);
    } catch {
      toast.error("Failed to add checkpoint");
    }
  };

  const handleDelete = async (id: string) => {
    const newCheckpoints = checkpoints.filter((c) => c.id !== id);

    try {
      await updateTask.mutateAsync({
        taskId: task.id,
        updates: { checkpoints: newCheckpoints },
      });
    } catch {
      toast.error("Failed to delete checkpoint");
    }
  };

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg flex items-center gap-2">
            <Bookmark className="h-5 w-5" />
            Checkpoints
          </CardTitle>
          <div className="flex items-center gap-2">
            <span className="text-sm text-muted-foreground">
              {checkpoints.length} saved
            </span>
            {!isAdding && (
              <Button size="sm" variant="ghost" onClick={() => setIsAdding(true)}>
                <Plus className="h-4 w-4 mr-1" />Add
              </Button>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {/* Add new checkpoint form */}
        {isAdding && (
          <div className="border rounded-lg p-4 mb-4 space-y-3">
            <div>
              <label className="text-sm font-medium mb-1 block">State Summary</label>
              <Textarea
                ref={summaryRef}
                value={newSummary}
                onChange={(e) => setNewSummary(e.target.value)}
                placeholder="Describe the current state..."
                className="text-sm min-h-[80px]"
              />
            </div>
            <div>
              <label className="text-sm font-medium mb-1 block">Remaining Work (one per line)</label>
              <Textarea
                value={newRemaining}
                onChange={(e) => setNewRemaining(e.target.value)}
                placeholder="Task 1&#10;Task 2&#10;Task 3"
                className="text-sm min-h-[60px]"
              />
            </div>
            <div>
              <label className="text-sm font-medium mb-1 block">Notes (optional)</label>
              <Input
                value={newNotes}
                onChange={(e) => setNewNotes(e.target.value)}
                placeholder="Additional notes..."
                className="text-sm"
              />
            </div>
            <div className="flex items-center gap-2">
              <div className="flex-1" />
              <Button size="sm" variant="ghost" onClick={() => { setNewSummary(""); setNewRemaining(""); setNewNotes(""); setIsAdding(false); }}>
                <X className="h-4 w-4" />
              </Button>
              <Button size="sm" onClick={handleAdd} disabled={!newSummary.trim() || updateTask.isPending}>
                <Check className="h-4 w-4 mr-1" />Save Checkpoint
              </Button>
            </div>
          </div>
        )}

        {sortedCheckpoints.length === 0 && !isAdding ? (
          <p
            className="text-muted-foreground italic cursor-pointer hover:bg-muted/30 p-2 rounded"
            onClick={() => setIsAdding(true)}
          >
            No checkpoints saved yet. Click to add one.
          </p>
        ) : (
          <div className="space-y-4">
            {sortedCheckpoints.map((checkpoint) => (
              <Card key={checkpoint.id} className="overflow-hidden group">
                <div className="bg-primary/10 px-4 py-2 flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Bookmark className="h-4 w-4 text-primary" />
                    <span className="font-medium text-sm">Checkpoint</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-muted-foreground flex items-center gap-1">
                      <Clock className="h-3 w-3" />
                      {formatTime(checkpoint.timestamp)}
                    </span>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => handleDelete(checkpoint.id)}
                      className="h-6 w-6 p-0 opacity-0 group-hover:opacity-100 text-destructive"
                    >
                      <Trash2 className="h-3 w-3" />
                    </Button>
                  </div>
                </div>
                <CardContent className="pt-4">
                  {/* Agent */}
                  <div className="flex items-center gap-2 text-sm text-muted-foreground mb-3">
                    <User className="h-4 w-4" />
                    <span>Saved by {getAgentDisplayName(checkpoint.agent_id)}</span>
                  </div>

                  {/* State Summary */}
                  <div className="mb-4">
                    <h4 className="text-sm font-medium mb-1">State Summary</h4>
                    <p className="text-sm text-muted-foreground whitespace-pre-wrap">
                      {checkpoint.state_summary}
                    </p>
                  </div>

                  {/* Remaining Work */}
                  {checkpoint.remaining_work.length > 0 && (
                    <div className="mb-4">
                      <div className="flex items-center gap-2 mb-2">
                        <ListTodo className="h-4 w-4 text-muted-foreground" />
                        <h4 className="text-sm font-medium">Remaining Work</h4>
                      </div>
                      <ul className="list-disc list-inside text-sm text-muted-foreground space-y-1">
                        {checkpoint.remaining_work.map((item, idx) => (
                          <li key={idx}>{item}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {/* Notes */}
                  {checkpoint.notes && (
                    <div>
                      <div className="flex items-center gap-2 mb-2">
                        <FileText className="h-4 w-4 text-muted-foreground" />
                        <h4 className="text-sm font-medium">Notes</h4>
                      </div>
                      <p className="text-sm text-muted-foreground whitespace-pre-wrap">
                        {checkpoint.notes}
                      </p>
                    </div>
                  )}
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ============================================================================
// Main TabProgress Component
// ============================================================================
export function TabProgress({ task }: TabProgressProps) {
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      {/* Progress Updates Column */}
      <div>
        <ProgressUpdatesSection task={task} />
      </div>

      {/* Checkpoints Column */}
      <div>
        <CheckpointsSection task={task} />
      </div>
    </div>
  );
}
