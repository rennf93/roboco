"use client";

import { useState } from "react";
import { GitStatusResponse } from "@/types/git";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  GitCommit,
  Upload,
  GitPullRequest,
  RefreshCw,
  ArrowUp,
} from "lucide-react";

interface GitActionsPanelProps {
  status: GitStatusResponse | undefined;
  projectSlug: string;
  taskId: string;
  agentId: string;
  onCommit: (message: string) => void;
  onPush: (force?: boolean) => void;
  onCreatePR: (title: string, body: string) => void;
  isCommitting: boolean;
  isPushing: boolean;
  isCreatingPR: boolean;
}

export function GitActionsPanel({
  status,
  projectSlug,
  taskId,
  agentId: _agentId,
  onCommit,
  onPush,
  onCreatePR,
  isCommitting,
  isPushing,
  isCreatingPR,
}: GitActionsPanelProps) {
  void _agentId; // Reserved for future use
  const [showCommitDialog, setShowCommitDialog] = useState(false);
  const [showPRDialog, setShowPRDialog] = useState(false);
  const [commitMessage, setCommitMessage] = useState("");
  const [prTitle, setPrTitle] = useState("");
  const [prBody, setPrBody] = useState("");

  const hasStagedChanges = (status?.staged_files.length ?? 0) > 0;
  const hasUnpushedCommits = (status?.ahead ?? 0) > 0;
  const canPush = hasUnpushedCommits;
  const canCreatePR = hasUnpushedCommits || status?.current_branch !== "main";

  const handleCommit = () => {
    if (commitMessage.trim()) {
      onCommit(commitMessage.trim());
      setShowCommitDialog(false);
      setCommitMessage("");
    }
  };

  const handleCreatePR = () => {
    if (prTitle.trim()) {
      onCreatePR(prTitle.trim(), prBody);
      setShowPRDialog(false);
      setPrTitle("");
      setPrBody("");
    }
  };

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">Git Actions</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* Commit Action */}
        <Dialog open={showCommitDialog} onOpenChange={setShowCommitDialog}>
          <DialogTrigger asChild>
            <Button
              className="w-full justify-start"
              variant={hasStagedChanges ? "default" : "outline"}
              disabled={!hasStagedChanges}
            >
              <GitCommit className="h-4 w-4 mr-2" />
              Commit Changes
              {hasStagedChanges && (
                <Badge variant="secondary" className="ml-auto">
                  {status?.staged_files.length} files
                </Badge>
              )}
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Commit Changes</DialogTitle>
            </DialogHeader>
            <div className="space-y-4 py-4">
              <div className="space-y-2">
                <label className="text-sm font-medium">Commit Message</label>
                <Textarea
                  placeholder="Describe your changes..."
                  value={commitMessage}
                  onChange={(e) => setCommitMessage(e.target.value)}
                  rows={4}
                />
              </div>
              <div className="text-sm text-muted-foreground">
                <p>Staged files: {status?.staged_files.length}</p>
                <ul className="mt-1 text-xs font-mono max-h-24 overflow-auto">
                  {status?.staged_files.slice(0, 5).map((f) => (
                    <li key={f} className="truncate">
                      {f}
                    </li>
                  ))}
                  {(status?.staged_files.length ?? 0) > 5 && (
                    <li>... and {(status?.staged_files.length ?? 0) - 5} more</li>
                  )}
                </ul>
              </div>
            </div>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => setShowCommitDialog(false)}
              >
                Cancel
              </Button>
              <Button
                onClick={handleCommit}
                disabled={!commitMessage.trim() || isCommitting}
              >
                {isCommitting && <RefreshCw className="h-4 w-4 mr-2 animate-spin" />}
                Commit
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* Push Action */}
        <Button
          className="w-full justify-start"
          variant={canPush ? "default" : "outline"}
          disabled={!canPush || isPushing}
          onClick={() => onPush(false)}
        >
          {isPushing ? (
            <RefreshCw className="h-4 w-4 mr-2 animate-spin" />
          ) : (
            <Upload className="h-4 w-4 mr-2" />
          )}
          Push to Remote
          {status?.ahead !== undefined && status.ahead > 0 && (
            <Badge variant="secondary" className="ml-auto">
              <ArrowUp className="h-3 w-3 mr-1" />
              {status.ahead}
            </Badge>
          )}
        </Button>

        {/* Create PR Action */}
        <Dialog open={showPRDialog} onOpenChange={setShowPRDialog}>
          <DialogTrigger asChild>
            <Button
              className="w-full justify-start"
              variant="outline"
              disabled={!canCreatePR}
            >
              <GitPullRequest className="h-4 w-4 mr-2" />
              Create Pull Request
            </Button>
          </DialogTrigger>
          <DialogContent className="max-w-lg">
            <DialogHeader>
              <DialogTitle>Create Pull Request</DialogTitle>
            </DialogHeader>
            <div className="space-y-4 py-4">
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Badge variant="outline">{status?.current_branch}</Badge>
                <span>→</span>
                <Badge variant="outline">main</Badge>
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium">Title</label>
                <Input
                  placeholder="PR title..."
                  value={prTitle}
                  onChange={(e) => setPrTitle(e.target.value)}
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium">Description</label>
                <Textarea
                  placeholder="Describe your changes..."
                  value={prBody}
                  onChange={(e) => setPrBody(e.target.value)}
                  rows={6}
                />
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setShowPRDialog(false)}>
                Cancel
              </Button>
              <Button
                onClick={handleCreatePR}
                disabled={!prTitle.trim() || isCreatingPR}
              >
                {isCreatingPR && <RefreshCw className="h-4 w-4 mr-2 animate-spin" />}
                Create PR
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* Status Summary */}
        {status && (
          <div className="pt-2 border-t text-xs text-muted-foreground space-y-1">
            <p>Project: {projectSlug}</p>
            <p>Branch: {status.current_branch}</p>
            {taskId && <p>Task: {taskId.slice(0, 8)}...</p>}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
