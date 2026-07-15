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
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import {
  GitCommit,
  Upload,
  GitPullRequest,
  GitMerge,
  RefreshCw,
  ArrowUp,
  Download,
  RefreshCcw,
  GitGraph,
} from "lucide-react";
import { HelpTip } from "@/components/ui/help-tip";

interface GitActionsPanelProps {
  status: GitStatusResponse | undefined;
  projectSlug: string;
  taskId: string;
  agentId: string;
  onCommit: (message: string) => void;
  onPush: (force?: boolean) => void;
  onCreatePR: (title: string, body: string) => void;
  onMergePR: (prNumber: number) => void;
  onPull: () => void;
  onFetch: () => void;
  onRebase: (targetBranch: string) => void;
  isCommitting: boolean;
  isPushing: boolean;
  isCreatingPR: boolean;
  isMerging: boolean;
  isPulling: boolean;
  isFetching: boolean;
  isRebasing: boolean;
}

export function GitActionsPanel({
  status,
  projectSlug,
  taskId,
  agentId: _agentId,
  onCommit,
  onPush,
  onCreatePR,
  onMergePR,
  onPull,
  onFetch,
  onRebase,
  isCommitting,
  isPushing,
  isCreatingPR,
  isMerging,
  isPulling,
  isFetching,
  isRebasing,
}: GitActionsPanelProps) {
  void _agentId; // Reserved for future use
  const [showCommitDialog, setShowCommitDialog] = useState(false);
  const [showPRDialog, setShowPRDialog] = useState(false);
  const [showMergeDialog, setShowMergeDialog] = useState(false);
  const [commitMessage, setCommitMessage] = useState("");
  const [prTitle, setPrTitle] = useState("");
  const [prBody, setPrBody] = useState("");
  const [mergePrNumber, setMergePrNumber] = useState("");
  const [rebaseTargetBranch, setRebaseTargetBranch] = useState("");

  const hasStagedChanges = (status?.staged_files.length ?? 0) > 0;
  const hasUnpushedCommits = (status?.ahead ?? 0) > 0;
  const canPush = hasUnpushedCommits;
  const canCreatePR = hasUnpushedCommits || status?.current_branch !== "main";

  const handleCommitDialogOpenChange = (newOpen: boolean) => {
    if (!newOpen) setCommitMessage("");
    setShowCommitDialog(newOpen);
  };

  const handlePRDialogOpenChange = (newOpen: boolean) => {
    if (!newOpen) {
      setPrTitle("");
      setPrBody("");
    }
    setShowPRDialog(newOpen);
  };

  const handleMergeDialogOpenChange = (newOpen: boolean) => {
    if (!newOpen) setMergePrNumber("");
    setShowMergeDialog(newOpen);
  };

  const handleMergePR = () => {
    const prNum = parseInt(mergePrNumber, 10);
    if (!isNaN(prNum) && prNum > 0) {
      onMergePR(prNum);
      setShowMergeDialog(false);
      setMergePrNumber("");
    }
  };

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
        <Dialog
          open={showCommitDialog}
          onOpenChange={handleCommitDialogOpenChange}
        >
          {/* HelpTip+span wraps the whole DialogTrigger, not the Button
              itself — the Button stays DialogTrigger's asChild target so
              Radix's Slot merge is undisturbed, and the outer span keeps the
              tip hoverable even while disabled (disabled sets
              pointer-events:none on the button, which would swallow hover on
              anything nested inside it). */}
          <HelpTip
            label={
              hasStagedChanges
                ? "Records the staged files as a local commit — doesn't push to the remote."
                : "Nothing staged yet — stage files first before a commit can be made."
            }
          >
            <span
              className="block w-full"
              tabIndex={!hasStagedChanges ? 0 : undefined}
            >
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
            </span>
          </HelpTip>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Commit Changes</DialogTitle>
            </DialogHeader>
            <div className="space-y-4 py-4">
              <div className="space-y-2">
                <HelpTip label="Becomes the commit's message — shows up in the log panel and the PR's commit history.">
                  <label className="text-sm font-medium w-fit">
                    Commit Message
                  </label>
                </HelpTip>
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
                    <li>
                      ... and {(status?.staged_files.length ?? 0) - 5} more
                    </li>
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
                {isCommitting && (
                  <RefreshCw className="h-4 w-4 mr-2 animate-spin" />
                )}
                Commit
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* Push Action */}
        <HelpTip
          label={
            canPush
              ? "git push -u origin <branch>. Uploads your local commits to GitHub — never force, safe to repeat."
              : "Nothing to push — no local commits sit ahead of the remote branch yet."
          }
        >
          <span
            className="block w-full"
            tabIndex={!canPush ? 0 : undefined}
          >
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
                <HelpTip label="Commits not yet pushed to the remote repository">
                  <Badge variant="secondary" className="ml-auto">
                    <ArrowUp className="h-3 w-3 mr-1" />
                    {status.ahead}
                  </Badge>
                </HelpTip>
              )}
            </Button>
          </span>
        </HelpTip>

        {/* Create PR Action */}
        <Dialog open={showPRDialog} onOpenChange={handlePRDialogOpenChange}>
          <HelpTip
            label={
              canCreatePR
                ? "Opens GitHub's PR creation flow for this branch against main."
                : "Already on main with nothing ahead of it — there's no branch content to open a PR for."
            }
          >
            <span
              className="block w-full"
              tabIndex={!canCreatePR ? 0 : undefined}
            >
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
            </span>
          </HelpTip>
          <DialogContent className="max-w-lg">
            <DialogHeader>
              <DialogTitle>Create Pull Request</DialogTitle>
            </DialogHeader>
            <div className="space-y-4 py-4">
              <HelpTip label="Source branch on the left merges into the target branch on the right.">
                <div className="flex items-center gap-2 text-sm text-muted-foreground w-fit">
                  <Badge variant="outline">{status?.current_branch}</Badge>
                  <span>→</span>
                  <Badge variant="outline">main</Badge>
                </div>
              </HelpTip>
              <div className="space-y-2">
                <HelpTip label="Becomes the pull request's title on GitHub.">
                  <label className="text-sm font-medium w-fit">Title</label>
                </HelpTip>
                <Input
                  placeholder="PR title..."
                  value={prTitle}
                  onChange={(e) => setPrTitle(e.target.value)}
                />
              </div>
              <div className="space-y-2">
                <HelpTip label="The PR's body on GitHub — markdown supported, shown to reviewers.">
                  <label className="text-sm font-medium w-fit">
                    Description
                  </label>
                </HelpTip>
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
                {isCreatingPR && (
                  <RefreshCw className="h-4 w-4 mr-2 animate-spin" />
                )}
                Create PR
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* Merge PR Action */}
        <Dialog
          open={showMergeDialog}
          onOpenChange={handleMergeDialogOpenChange}
        >
          <HelpTip label="Merges an open PR via GitHub's API — squash by default, falls back to merge/rebase if blocked by branch protection.">
            <span className="block w-full">
              <DialogTrigger asChild>
                <Button className="w-full justify-start" variant="outline">
                  {isMerging ? (
                    <RefreshCw className="h-4 w-4 mr-2 animate-spin" />
                  ) : (
                    <GitMerge className="h-4 w-4 mr-2" />
                  )}
                  Merge PR
                </Button>
              </DialogTrigger>
            </span>
          </HelpTip>
          <DialogContent className="max-w-sm">
            <DialogHeader>
              <DialogTitle>Merge Pull Request</DialogTitle>
            </DialogHeader>
            <div className="space-y-4 py-4">
              <div className="space-y-2">
                <HelpTip label="The GitHub PR number to merge, e.g. the 42 in .../pull/42.">
                  <label className="text-sm font-medium w-fit">
                    PR Number
                  </label>
                </HelpTip>
                <Input
                  type="number"
                  placeholder="e.g. 42"
                  value={mergePrNumber}
                  onChange={(e) => setMergePrNumber(e.target.value)}
                  min={1}
                />
              </div>
            </div>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => setShowMergeDialog(false)}
              >
                Cancel
              </Button>
              <Button
                onClick={handleMergePR}
                disabled={
                  !mergePrNumber.trim() ||
                  isNaN(parseInt(mergePrNumber, 10)) ||
                  isMerging
                }
              >
                {isMerging && (
                  <RefreshCw className="h-4 w-4 mr-2 animate-spin" />
                )}
                Merge
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* Pull Action */}
        <HelpTip label="Fetches origin, then fast-forwards your branch onto it. Refuses (no merge commit) if history has diverged.">
          <span className="block w-full">
            <Button
              className="w-full justify-start"
              variant="outline"
              disabled={isPulling}
              onClick={onPull}
            >
              {isPulling ? (
                <RefreshCw className="h-4 w-4 mr-2 animate-spin" />
              ) : (
                <Download className="h-4 w-4 mr-2" />
              )}
              Pull from Remote
            </Button>
          </span>
        </HelpTip>

        {/* Fetch Action */}
        <HelpTip label="Updates remote-tracking refs from origin only — never touches your working directory or local branch.">
          <span className="block w-full">
            <Button
              className="w-full justify-start"
              variant="outline"
              disabled={isFetching}
              onClick={onFetch}
            >
              {isFetching ? (
                <RefreshCw className="h-4 w-4 mr-2 animate-spin" />
              ) : (
                <RefreshCcw className="h-4 w-4 mr-2" />
              )}
              Fetch Remote
            </Button>
          </span>
        </HelpTip>

        {/* Rebase Action — destructive, requires confirmation */}
        <AlertDialog>
          <HelpTip label="Rewrites this branch's commit history on top of another branch. Requires a force-push afterward — use with care.">
            <span className="block w-full">
              <AlertDialogTrigger asChild>
                <Button
                  className="w-full justify-start"
                  variant="outline"
                  disabled={isRebasing}
                >
                  {isRebasing ? (
                    <RefreshCw className="h-4 w-4 mr-2 animate-spin" />
                  ) : (
                    <GitGraph className="h-4 w-4 mr-2" />
                  )}
                  Rebase onto Remote
                </Button>
              </AlertDialogTrigger>
            </span>
          </HelpTip>
          <AlertDialogContent className="border-destructive bg-destructive/5">
            <AlertDialogHeader>
              <AlertDialogTitle>Rebase onto target branch?</AlertDialogTitle>
              <AlertDialogDescription>
                This will rewrite the commit history of branch{" "}
                <strong>{status?.current_branch}</strong> by replaying commits
                on top of the specified target branch. A force-push will be
                required afterward. This action cannot be undone.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <div className="px-6 py-2 space-y-2">
              <HelpTip label="Branch/ref to replay commits onto, e.g. origin/main. On conflict, rebase auto-aborts — your tree stays untouched.">
                <label className="text-sm font-medium w-fit">
                  Target branch
                </label>
              </HelpTip>
              <Input
                placeholder="Remote ref (e.g. origin/HEAD)"
                value={rebaseTargetBranch}
                onChange={(e) => setRebaseTargetBranch(e.target.value)}
              />
            </div>
            <AlertDialogFooter>
              <AlertDialogCancel onClick={() => setRebaseTargetBranch("")}>
                Cancel
              </AlertDialogCancel>
              <AlertDialogAction
                className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                disabled={!rebaseTargetBranch.trim()}
                onClick={() => {
                  onRebase(rebaseTargetBranch.trim());
                  setRebaseTargetBranch("");
                }}
              >
                Rebase
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>

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
