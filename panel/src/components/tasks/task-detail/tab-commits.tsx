"use client";

import { useState, useRef, useEffect } from "react";
import { Task, CommitRef } from "@/types";
import { useUpdateTask } from "@/hooks/use-tasks";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { GitCommit, GitBranch, ExternalLink, Clock, User, Plus, Trash2, X, Check } from "lucide-react";
import { toast } from "sonner";
import { getAgentDisplayName } from "@/lib/agent-utils";

interface TabCommitsProps {
  task: Task;
}

function formatTime(timestamp: string): string {
  const date = new Date(timestamp);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
  const diffDays = Math.floor(diffHours / 24);

  if (diffHours < 1) return "Just now";
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  });
}

export function TabCommits({ task }: TabCommitsProps) {
  const updateTask = useUpdateTask();
  const commits = task.commits;

  const [isAdding, setIsAdding] = useState(false);
  const [newHash, setNewHash] = useState("");
  const [newMessage, setNewMessage] = useState("");

  const hashRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isAdding && hashRef.current) hashRef.current.focus();
  }, [isAdding]);

  // Sort commits by timestamp (newest first)
  const sortedCommits = [...commits].sort(
    (a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime()
  );

  const handleAdd = async () => {
    if (!newHash.trim() || !newMessage.trim()) {
      if (!newHash.trim() && !newMessage.trim()) {
        setIsAdding(false);
        return;
      }
      toast.error("Both hash and message are required");
      return;
    }

    // Check for duplicate hash
    if (commits.some((c) => c.hash === newHash.trim())) {
      toast.error("This commit hash is already linked");
      return;
    }

    const newCommit: CommitRef = {
      hash: newHash.trim(),
      message: newMessage.trim(),
      timestamp: new Date().toISOString(),
      author_agent_id: "CEO",
    };

    try {
      await updateTask.mutateAsync({
        taskId: task.id,
        updates: { commits: [...commits, newCommit] },
      });
      setNewHash("");
      setNewMessage("");
      setIsAdding(false);
    } catch {
      toast.error("Failed to link commit");
    }
  };

  const handleDelete = async (hash: string) => {
    const newCommits = commits.filter((c) => c.hash !== hash);

    try {
      await updateTask.mutateAsync({
        taskId: task.id,
        updates: { commits: newCommits },
      });
    } catch {
      toast.error("Failed to unlink commit");
    }
  };

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="grid grid-cols-3 items-center gap-4">
          <CardTitle className="text-lg flex items-center gap-2">
            <GitCommit className="h-5 w-5" />
            Linked Commits
            <span className="text-sm font-normal text-muted-foreground">
              ({commits.length})
            </span>
          </CardTitle>
          <div className="flex items-center justify-center gap-2">
            {task.branch_name && (
              <Badge variant="outline" className="gap-1.5 py-1 px-2.5 font-mono text-sm">
                <GitBranch className="h-4 w-4" />
                {task.branch_name}
              </Badge>
            )}
            {task.pr_url && (
              <a
                href={task.pr_url}
                target="_blank"
                rel="noopener noreferrer"
              >
                <Badge variant="secondary" className="gap-1.5 py-1 px-2.5 hover:bg-secondary/80">
                  PR #{task.pr_number}
                  <ExternalLink className="h-3.5 w-3.5" />
                </Badge>
              </a>
            )}
          </div>
          <div className="flex justify-end">
            {!isAdding && (
              <Button size="sm" variant="ghost" onClick={() => setIsAdding(true)}>
                <Plus className="h-4 w-4 mr-1" />Link
              </Button>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent>

        {/* Add new commit form */}
        {isAdding && (
          <div className="border rounded-lg p-4 mb-4 space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-sm font-medium mb-1 block">Commit Hash</label>
                <Input
                  ref={hashRef}
                  value={newHash}
                  onChange={(e) => setNewHash(e.target.value)}
                  placeholder="abc1234..."
                  className="font-mono text-sm"
                />
              </div>
              <div>
                <label className="text-sm font-medium mb-1 block">Commit Message</label>
                <Input
                  value={newMessage}
                  onChange={(e) => setNewMessage(e.target.value)}
                  placeholder="fix: resolved issue..."
                  className="text-sm"
                />
              </div>
            </div>
            <div className="flex items-center gap-2">
              <div className="flex-1" />
              <Button size="sm" variant="ghost" onClick={() => { setNewHash(""); setNewMessage(""); setIsAdding(false); }}>
                <X className="h-4 w-4" />
              </Button>
              <Button size="sm" onClick={handleAdd} disabled={(!newHash.trim() || !newMessage.trim()) || updateTask.isPending}>
                <Check className="h-4 w-4 mr-1" />Link Commit
              </Button>
            </div>
          </div>
        )}

        {sortedCommits.length === 0 && !isAdding ? (
          <div className="text-center text-muted-foreground py-8">
            <GitCommit className="h-12 w-12 mx-auto mb-4 opacity-50" />
            <p>No commits linked to this task yet.</p>
            <p className="text-sm mt-2 mb-4">
              Commits will be linked as developers push code for this task.
            </p>
            <Button variant="outline" onClick={() => setIsAdding(true)}>
              <Plus className="h-4 w-4 mr-2" />
              Link Commit Manually
            </Button>
          </div>
        ) : (
          <div className="space-y-3">
            {sortedCommits.map((commit) => (
              <Card key={commit.hash} className="overflow-hidden group">
                <CardContent className="pt-4">
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex items-start gap-3 flex-1 min-w-0">
                      <div className="bg-primary/10 rounded-full p-2">
                        <GitCommit className="h-4 w-4 text-primary" />
                      </div>
                      <div className="flex-1 min-w-0">
                        {/* Commit message */}
                        <p className="font-medium text-sm leading-tight mb-1">
                          {commit.message}
                        </p>

                        {/* Meta info */}
                        <div className="flex items-center gap-3 text-xs text-muted-foreground">
                          {/* Hash */}
                          <Badge variant="outline" className="font-mono text-xs">
                            {commit.hash.slice(0, 7)}
                          </Badge>

                          {/* Author */}
                          {commit.author_agent_id && (
                            <span className="flex items-center gap-1">
                              <User className="h-3 w-3" />
                              {getAgentDisplayName(commit.author_agent_id)}
                            </span>
                          )}

                          {/* Time */}
                          <span className="flex items-center gap-1">
                            <Clock className="h-3 w-3" />
                            {formatTime(commit.timestamp)}
                          </span>
                        </div>
                      </div>
                    </div>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => handleDelete(commit.hash)}
                      className="h-7 w-7 p-0 opacity-0 group-hover:opacity-100 text-destructive"
                    >
                      <Trash2 className="h-3 w-3" />
                    </Button>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
