"use client";

import { useState } from "react";
import { GitBranchListResponse, BranchType } from "@/types/git";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  GitBranch,
  Check,
  Cloud,
  Plus,
  RefreshCw,
} from "lucide-react";

interface GitBranchPanelProps {
  branches: GitBranchListResponse | undefined;
  isLoading: boolean;
  onCheckout: (branch: string) => void;
  onCreateBranch: (branchType: BranchType, taskId: string) => void;
  isCheckingOut: boolean;
  isCreating: boolean;
}

export function GitBranchPanel({
  branches,
  isLoading,
  onCheckout,
  onCreateBranch,
  isCheckingOut,
  isCreating,
}: GitBranchPanelProps) {
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [newBranchType, setNewBranchType] = useState<BranchType>("feature");
  const [taskId, setTaskId] = useState("");

  const handleCreateBranch = () => {
    if (taskId.trim()) {
      onCreateBranch(newBranchType, taskId.trim());
      setShowCreateDialog(false);
      setTaskId("");
    }
  };

  if (isLoading) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <Skeleton className="h-5 w-24" />
        </CardHeader>
        <CardContent className="space-y-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-8 w-full" />
          ))}
        </CardContent>
      </Card>
    );
  }

  if (!branches) {
    return (
      <Card>
        <CardContent className="py-8 text-center text-muted-foreground">
          <GitBranch className="h-8 w-8 mx-auto mb-2 opacity-50" />
          <p>No branches available</p>
        </CardContent>
      </Card>
    );
  }

  const localBranches = branches.branches.filter((b) => !b.is_remote);
  const remoteBranches = branches.branches.filter((b) => b.is_remote);

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center justify-between">
          <span className="flex items-center gap-2">
            <GitBranch className="h-4 w-4" />
            Branches
          </span>
          <Dialog open={showCreateDialog} onOpenChange={setShowCreateDialog}>
            <DialogTrigger asChild>
              <Button size="sm" variant="outline">
                <Plus className="h-3 w-3 mr-1" />
                New
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Create Task Branch</DialogTitle>
              </DialogHeader>
              <div className="space-y-4 py-4">
                <div className="space-y-2">
                  <label className="text-sm font-medium">Branch Type</label>
                  <Select
                    value={newBranchType}
                    onValueChange={(v) => setNewBranchType(v as BranchType)}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="feature">Feature</SelectItem>
                      <SelectItem value="bug">Bug Fix</SelectItem>
                      <SelectItem value="chore">Chore</SelectItem>
                      <SelectItem value="docs">Documentation</SelectItem>
                      <SelectItem value="hotfix">Hotfix</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium">Task ID</label>
                  <Input
                    placeholder="Enter task ID..."
                    value={taskId}
                    onChange={(e) => setTaskId(e.target.value)}
                  />
                </div>
              </div>
              <DialogFooter>
                <Button
                  variant="outline"
                  onClick={() => setShowCreateDialog(false)}
                >
                  Cancel
                </Button>
                <Button
                  onClick={handleCreateBranch}
                  disabled={!taskId.trim() || isCreating}
                >
                  {isCreating && <RefreshCw className="h-4 w-4 mr-2 animate-spin" />}
                  Create Branch
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <ScrollArea className="h-64">
          <div className="space-y-3">
            {/* Local Branches */}
            <div>
              <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1">
                Local ({localBranches.length})
              </h4>
              <div className="space-y-0.5">
                {localBranches.map((branch) => (
                  <button
                    key={branch.name}
                    onClick={() => !branch.is_current && onCheckout(branch.name)}
                    disabled={branch.is_current || isCheckingOut}
                    className={
                      "w-full flex items-center justify-between px-2 py-1.5 rounded text-sm text-left transition-colors " +
                      (branch.is_current
                        ? "bg-primary/10 text-primary"
                        : "hover:bg-muted")
                    }
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      {branch.is_current && (
                        <Check className="h-3 w-3 text-primary shrink-0" />
                      )}
                      <span className="truncate font-mono text-xs">
                        {branch.name}
                      </span>
                    </div>
                    {branch.last_commit && (
                      <span className="text-xs text-muted-foreground font-mono shrink-0">
                        {branch.last_commit.slice(0, 7)}
                      </span>
                    )}
                  </button>
                ))}
              </div>
            </div>

            {/* Remote Branches */}
            {remoteBranches.length > 0 && (
              <div>
                <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-1 flex items-center gap-1">
                  <Cloud className="h-3 w-3" />
                  Remote ({remoteBranches.length})
                </h4>
                <div className="space-y-0.5">
                  {remoteBranches.map((branch) => (
                    <button
                      key={branch.name}
                      onClick={() => onCheckout(branch.name)}
                      disabled={isCheckingOut}
                      className="w-full flex items-center justify-between px-2 py-1.5 rounded text-sm text-left transition-colors hover:bg-muted"
                    >
                      <span className="truncate font-mono text-xs text-muted-foreground">
                        {branch.name}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        </ScrollArea>
      </CardContent>
    </Card>
  );
}
