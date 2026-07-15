"use client";

import { GitStatusResponse } from "@/types/git";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  GitBranch,
  FileCode,
  FilePlus,
  FileX,
  ArrowUp,
  ArrowDown,
  CheckCircle,
} from "lucide-react";
import { HelpTip } from "@/components/ui/help-tip";

interface GitStatusPanelProps {
  status: GitStatusResponse | undefined;
  isLoading: boolean;
}

// Plain-language explanation per file bucket, mirroring the task-status-badge
// per-state description map so non-git-fluent readers (CEO, PM) know what
// each section means without having to already know git jargon.
const FILE_SECTION_DESCRIPTIONS = {
  staged: "Changes staged and ready to be included in the next commit.",
  unstaged: "Tracked files with changes not yet staged for commit.",
  untracked: "New files git isn't tracking yet.",
} as const;

export function GitStatusPanel({ status, isLoading }: GitStatusPanelProps) {
  if (isLoading) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <Skeleton className="h-5 w-32" />
        </CardHeader>
        <CardContent className="space-y-3">
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-24 w-full" />
        </CardContent>
      </Card>
    );
  }

  if (!status) {
    return (
      <Card>
        <CardContent className="py-8 text-center text-muted-foreground">
          <GitBranch className="h-8 w-8 mx-auto mb-2 opacity-50" />
          <p>No git status available</p>
        </CardContent>
      </Card>
    );
  }

  const hasChanges =
    status.staged_files.length > 0 ||
    status.unstaged_files.length > 0 ||
    status.untracked_files.length > 0;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center justify-between">
          <span className="flex items-center gap-2">
            <GitBranch className="h-4 w-4" />
            Repository Status
          </span>
          {!hasChanges && (
            <Badge variant="outline" className="text-green-600">
              <CheckCircle className="h-3 w-3 mr-1" />
              Clean
            </Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Branch Info */}
        <div className="flex items-center gap-4 text-sm">
          <div className="flex items-center gap-2">
            <GitBranch className="h-4 w-4 text-muted-foreground" />
            <span className="font-medium">{status.current_branch}</span>
          </div>
          {(status.ahead > 0 || status.behind > 0) && (
            <div className="flex items-center gap-2">
              {status.ahead > 0 && (
                <HelpTip label="Local commits not yet pushed to the remote.">
                  <Badge variant="secondary" className="text-xs">
                    <ArrowUp className="h-3 w-3 mr-1" />
                    {status.ahead} ahead
                  </Badge>
                </HelpTip>
              )}
              {status.behind > 0 && (
                <HelpTip label="Remote commits not yet merged into your local branch.">
                  <Badge variant="secondary" className="text-xs">
                    <ArrowDown className="h-3 w-3 mr-1" />
                    {status.behind} behind
                  </Badge>
                </HelpTip>
              )}
            </div>
          )}
        </div>

        {/* File Changes */}
        {hasChanges && (
          <ScrollArea className="h-48">
            <div className="space-y-3">
              {/* Staged Files */}
              {status.staged_files.length > 0 && (
                <div>
                  <HelpTip label={FILE_SECTION_DESCRIPTIONS.staged}>
                    <h4 className="text-xs font-semibold text-green-600 uppercase tracking-wider mb-1 w-fit">
                      Staged ({status.staged_files.length})
                    </h4>
                  </HelpTip>
                  <div className="space-y-0.5">
                    {status.staged_files.map((file) => (
                      <div
                        key={file}
                        className="flex items-center gap-2 text-sm py-0.5 px-2 rounded hover:bg-muted"
                      >
                        <FileCode className="h-3.5 w-3.5 text-green-600" />
                        <span className="truncate font-mono text-xs">
                          {file}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Unstaged Files */}
              {status.unstaged_files.length > 0 && (
                <div>
                  <HelpTip label={FILE_SECTION_DESCRIPTIONS.unstaged}>
                    <h4 className="text-xs font-semibold text-orange-600 uppercase tracking-wider mb-1 w-fit">
                      Modified ({status.unstaged_files.length})
                    </h4>
                  </HelpTip>
                  <div className="space-y-0.5">
                    {status.unstaged_files.map((file) => (
                      <div
                        key={file}
                        className="flex items-center gap-2 text-sm py-0.5 px-2 rounded hover:bg-muted"
                      >
                        <FileX className="h-3.5 w-3.5 text-orange-600" />
                        <span className="truncate font-mono text-xs">
                          {file}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Untracked Files */}
              {status.untracked_files.length > 0 && (
                <div>
                  <HelpTip label={FILE_SECTION_DESCRIPTIONS.untracked}>
                    <h4 className="text-xs font-semibold text-blue-600 uppercase tracking-wider mb-1 w-fit">
                      Untracked ({status.untracked_files.length})
                    </h4>
                  </HelpTip>
                  <div className="space-y-0.5">
                    {status.untracked_files.map((file) => (
                      <div
                        key={file}
                        className="flex items-center gap-2 text-sm py-0.5 px-2 rounded hover:bg-muted"
                      >
                        <FilePlus className="h-3.5 w-3.5 text-blue-600" />
                        <span className="truncate font-mono text-xs">
                          {file}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </ScrollArea>
        )}

        {/* No Changes */}
        {!hasChanges && (
          <div className="text-center py-4 text-muted-foreground text-sm">
            Working directory is clean
          </div>
        )}
      </CardContent>
    </Card>
  );
}
