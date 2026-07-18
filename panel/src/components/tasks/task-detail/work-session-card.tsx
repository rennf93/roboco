"use client";

import { useWorkSessionForTask } from "@/hooks/use-work-sessions";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { HelpTip } from "@/components/ui/help-tip";
import {
  GitBranch,
  GitPullRequest,
  GitCommit,
  ExternalLink,
  CheckCircle2,
  Clock,
  XCircle,
  FileCode,
} from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import { formatAbsoluteTimestamp } from "@/lib/utils";
import { WorkSessionStatus } from "@/types";

interface WorkSessionCardProps {
  taskId: string;
}

// Per-state description maps (task-status-badge.tsx idiom), local to the
// WorkSession domain — a distinct lifecycle from TaskStatus.
const SESSION_STATUS_DESCRIPTIONS: Record<string, string> = {
  [WorkSessionStatus.ACTIVE]: "This session's branch is still being worked on.",
  [WorkSessionStatus.COMPLETED]: "The branch was merged and the session closed out.",
  [WorkSessionStatus.ABANDONED]: "The session was dropped without merging.",
};

const PR_STATUS_DESCRIPTIONS: Record<string, string> = {
  open: "The pull request is open and awaiting review or merge.",
  merged: "The pull request was merged.",
  closed: "The pull request was closed without merging.",
  draft: "The pull request is a draft, not yet ready for review.",
};

function getStatusBadge(status: WorkSessionStatus) {
  const tip = SESSION_STATUS_DESCRIPTIONS[status] ?? "";
  switch (status) {
    case WorkSessionStatus.ACTIVE:
      return (
        <HelpTip label={tip}>
          <Badge className="bg-blue-500/10 text-blue-500">
            <Clock className="h-3 w-3 mr-1" />
            Active
          </Badge>
        </HelpTip>
      );
    case WorkSessionStatus.COMPLETED:
      return (
        <HelpTip label={tip}>
          <Badge className="bg-green-500/10 text-green-500">
            <CheckCircle2 className="h-3 w-3 mr-1" />
            Completed
          </Badge>
        </HelpTip>
      );
    case WorkSessionStatus.ABANDONED:
      return (
        <HelpTip label={tip}>
          <Badge variant="destructive">
            <XCircle className="h-3 w-3 mr-1" />
            Abandoned
          </Badge>
        </HelpTip>
      );
    default:
      return <Badge variant="outline">{status}</Badge>;
  }
}

function getPRStatusBadge(prStatus: string | null) {
  if (!prStatus) return null;
  const tip = PR_STATUS_DESCRIPTIONS[prStatus] ?? "";

  switch (prStatus) {
    case "open":
      return (
        <HelpTip label={tip}>
          <Badge className="bg-green-500/10 text-green-500">
            <GitPullRequest className="h-3 w-3 mr-1" />
            Open
          </Badge>
        </HelpTip>
      );
    case "merged":
      return (
        <HelpTip label={tip}>
          <Badge className="bg-purple-500/10 text-purple-500">
            <GitPullRequest className="h-3 w-3 mr-1" />
            Merged
          </Badge>
        </HelpTip>
      );
    case "closed":
      return (
        <HelpTip label={tip}>
          <Badge variant="destructive">
            <GitPullRequest className="h-3 w-3 mr-1" />
            Closed
          </Badge>
        </HelpTip>
      );
    case "draft":
      return (
        <HelpTip label={tip}>
          <Badge variant="outline">
            <GitPullRequest className="h-3 w-3 mr-1" />
            Draft
          </Badge>
        </HelpTip>
      );
    default:
      return (
        <Badge variant="outline">
          <GitPullRequest className="h-3 w-3 mr-1" />
          {prStatus}
        </Badge>
      );
  }
}

export function WorkSessionCard({ taskId }: WorkSessionCardProps) {
  const { data: session, isLoading } = useWorkSessionForTask(taskId);

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-lg flex items-center gap-2">
            <GitBranch className="h-5 w-5" />
            Work Session
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-2">
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-3/4" />
          </div>
        </CardContent>
      </Card>
    );
  }

  if (!session) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-lg flex items-center gap-2">
            <GitBranch className="h-5 w-5" />
            Work Session
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-center py-4 text-muted-foreground">
            <GitBranch className="h-8 w-8 mx-auto mb-2 opacity-50" />
            <p className="text-sm">No active work session</p>
            <p className="text-xs mt-1">
              A work session will be created when development begins
            </p>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg flex items-center gap-2">
            <GitBranch className="h-5 w-5" />
            Work Session
          </CardTitle>
          {getStatusBadge(session.status)}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Branch Info */}
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <GitBranch className="h-4 w-4 text-muted-foreground" />
            <span className="text-sm font-medium">Branch:</span>
            <code className="text-sm bg-muted px-2 py-0.5 rounded font-mono">
              {session.branch_name}
            </code>
          </div>
          <HelpTip label="Base is the branch this session forked from; target is where its PR merges into">
            <div className="flex items-center gap-2 text-sm text-muted-foreground w-fit">
              <span>Base:</span>
              <code className="bg-muted px-1.5 py-0.5 rounded text-xs font-mono">
                {session.base_branch}
              </code>
              <span>→</span>
              <code className="bg-muted px-1.5 py-0.5 rounded text-xs font-mono">
                {session.target_branch}
              </code>
            </div>
          </HelpTip>
        </div>

        {/* PR Info */}
        {session.pr_number && (
          <div className="flex items-center justify-between p-3 bg-muted/50 rounded-lg">
            <div className="flex items-center gap-3">
              <GitPullRequest className="h-5 w-5 text-muted-foreground" />
              <div>
                <div className="flex items-center gap-2">
                  <span className="font-medium">PR #{session.pr_number}</span>
                  {getPRStatusBadge(session.pr_status)}
                </div>
                {session.pr_created_at && (
                  <HelpTip label={formatAbsoluteTimestamp(session.pr_created_at)}>
                    <p className="text-xs text-muted-foreground w-fit">
                      Created{" "}
                      {formatDistanceToNow(new Date(session.pr_created_at), {
                        addSuffix: true,
                      })}
                    </p>
                  </HelpTip>
                )}
              </div>
            </div>
            {session.pr_url && (
              <HelpTip label="Opens the pull request on GitHub in a new tab">
                <Button variant="outline" size="sm" asChild>
                  <a
                    href={session.pr_url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    <ExternalLink className="h-4 w-4 mr-1" />
                    View PR
                  </a>
                </Button>
              </HelpTip>
            )}
          </div>
        )}

        {/* Stats Row */}
        <div className="flex items-center gap-4 pt-2">
          <HelpTip label="Commits recorded against this branch's work session">
            <div className="flex items-center gap-2 w-fit">
              <GitCommit className="h-4 w-4 text-muted-foreground" />
              <span className="text-sm">
                {session.commits.length} commit
                {session.commits.length !== 1 ? "s" : ""}
              </span>
            </div>
          </HelpTip>
          <HelpTip label="Distinct files touched across this session's commits">
            <div className="flex items-center gap-2 w-fit">
              <FileCode className="h-4 w-4 text-muted-foreground" />
              <span className="text-sm">
                {session.files_modified.length} file
                {session.files_modified.length !== 1 ? "s" : ""}
              </span>
            </div>
          </HelpTip>
          <HelpTip label={formatAbsoluteTimestamp(session.started_at)}>
            <div className="text-sm text-muted-foreground ml-auto w-fit">
              Started{" "}
              {formatDistanceToNow(new Date(session.started_at), {
                addSuffix: true,
              })}
            </div>
          </HelpTip>
        </div>
      </CardContent>
    </Card>
  );
}
