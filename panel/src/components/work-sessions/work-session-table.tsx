"use client";

import { formatDistanceToNow } from "date-fns";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  ResponsiveTable,
  ResponsiveTableCardList,
  ResponsiveTableCard,
  ResponsiveTableCardRow,
} from "@/components/ui/responsive-table";
import { Skeleton } from "@/components/ui/skeleton";
import { HelpTip } from "@/components/ui/help-tip";
import {
  GitBranch,
  GitPullRequest,
  ExternalLink,
  CheckCircle2,
  XCircle,
  Clock,
  GitCommit,
} from "lucide-react";
import type { WorkSessionSummary, WorkSessionStatus } from "@/types";

interface WorkSessionTableProps {
  sessions: WorkSessionSummary[] | undefined;
  isLoading: boolean;
}

// Mirrors the status hints in work-session-filters.tsx's dropdown — grounded
// in roboco/services/work_session.py's transition triggers.
const STATUS_HINTS: Record<WorkSessionStatus, string> = {
  active: "An agent is currently working this branch.",
  completed: "The session's PR was merged — the branch's work is done.",
  abandoned:
    "Superseded by a re-claim, cancellation, or project deletion — never an auto-timeout.",
};

function getStatusBadge(status: WorkSessionStatus) {
  switch (status) {
    case "active":
      return (
        <Badge className="bg-blue-500/10 text-blue-500">
          <Clock className="h-3 w-3 mr-1" />
          Active
        </Badge>
      );
    case "completed":
      return (
        <Badge className="bg-green-500/10 text-green-500">
          <CheckCircle2 className="h-3 w-3 mr-1" />
          Completed
        </Badge>
      );
    case "abandoned":
      return (
        <Badge variant="destructive">
          <XCircle className="h-3 w-3 mr-1" />
          Abandoned
        </Badge>
      );
    default:
      return <Badge variant="outline">{status}</Badge>;
  }
}

function StatusBadge({ status }: { status: WorkSessionStatus }) {
  return (
    <HelpTip label={STATUS_HINTS[status]}>
      <span className="w-fit">{getStatusBadge(status)}</span>
    </HelpTip>
  );
}

function PrBadge({ hasPr }: { hasPr: boolean }) {
  return hasPr ? (
    <HelpTip label="This session's branch has an open pull request on GitHub.">
      <Badge className="bg-purple-500/10 text-purple-500 w-fit">
        <GitPullRequest className="h-3 w-3 mr-1" />
        PR Open
      </Badge>
    </HelpTip>
  ) : (
    <HelpTip label="No pull request opened yet for this session's branch.">
      <span className="text-sm text-muted-foreground w-fit">No PR</span>
    </HelpTip>
  );
}

export function WorkSessionTable({
  sessions,
  isLoading,
}: WorkSessionTableProps) {
  if (isLoading) {
    return (
      <div className="space-y-3">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-14 w-full" />
        ))}
      </div>
    );
  }

  if (!sessions || sessions.length === 0) {
    return (
      <div className="text-center py-12 text-muted-foreground">
        <GitCommit className="h-12 w-12 mx-auto mb-4 opacity-50" />
        <p className="text-lg font-medium">No work sessions found</p>
        <p className="text-sm">
          Work sessions are created when agents start working on tasks
        </p>
      </div>
    );
  }

  return (
    <ResponsiveTable
      table={
        <div className="border rounded-lg">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Branch</TableHead>
                <TableHead>Task</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>PR</TableHead>
                <TableHead>Started</TableHead>
                <TableHead className="w-[80px]">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sessions.map((session) => (
                <TableRow key={session.id}>
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <GitBranch className="h-4 w-4 text-muted-foreground" />
                      <HelpTip label="Open this session's detail page.">
                        <Link
                          prefetch={false}
                          href={`/work-sessions/${session.id}`}
                          className="font-medium hover:underline font-mono text-sm"
                        >
                          {session.branch_name}
                        </Link>
                      </HelpTip>
                    </div>
                  </TableCell>
                  <TableCell>
                    <HelpTip label={`Open task ${session.task_id}`}>
                      <Link
                        prefetch={false}
                        href={`/tasks/${session.task_id}`}
                        className="text-sm text-muted-foreground hover:text-foreground hover:underline"
                      >
                        {session.task_id.slice(0, 8)}...
                      </Link>
                    </HelpTip>
                  </TableCell>
                  <TableCell>
                    <StatusBadge status={session.status} />
                  </TableCell>
                  <TableCell>
                    <PrBadge hasPr={session.has_pr} />
                  </TableCell>
                  <TableCell className="text-sm text-muted-foreground">
                    <HelpTip
                      label={new Date(session.started_at).toLocaleString()}
                    >
                      <span className="w-fit">
                        {formatDistanceToNow(new Date(session.started_at), {
                          addSuffix: true,
                        })}
                      </span>
                    </HelpTip>
                  </TableCell>
                  <TableCell>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Link
                          href={`/work-sessions/${session.id}`}
                          prefetch={false}
                        >
                          <Button variant="ghost" size="icon">
                            <ExternalLink className="h-4 w-4" />
                          </Button>
                        </Link>
                      </TooltipTrigger>
                      <TooltipContent>Open work session</TooltipContent>
                    </Tooltip>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      }
      cards={
        <ResponsiveTableCardList>
          {sessions.map((session) => (
            <ResponsiveTableCard key={session.id}>
              <div className="flex items-start justify-between gap-2">
                <div className="flex min-w-0 items-center gap-2">
                  <GitBranch className="h-4 w-4 shrink-0 text-muted-foreground" />
                  <HelpTip
                    label={`Open this session's detail page — ${session.branch_name}`}
                  >
                    <Link
                      prefetch={false}
                      href={`/work-sessions/${session.id}`}
                      className="truncate font-mono text-sm font-medium hover:underline"
                    >
                      {session.branch_name}
                    </Link>
                  </HelpTip>
                </div>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Link
                      href={`/work-sessions/${session.id}`}
                      prefetch={false}
                    >
                      <Button variant="ghost" size="icon" className="shrink-0">
                        <ExternalLink className="h-4 w-4" />
                      </Button>
                    </Link>
                  </TooltipTrigger>
                  <TooltipContent>Open work session</TooltipContent>
                </Tooltip>
              </div>
              <div className="mt-3 divide-y">
                <ResponsiveTableCardRow label="Task">
                  <HelpTip label={`Open task ${session.task_id}`}>
                    <Link
                      prefetch={false}
                      href={`/tasks/${session.task_id}`}
                      className="text-muted-foreground hover:text-foreground hover:underline"
                    >
                      {session.task_id.slice(0, 8)}...
                    </Link>
                  </HelpTip>
                </ResponsiveTableCardRow>
                <ResponsiveTableCardRow label="Status">
                  <StatusBadge status={session.status} />
                </ResponsiveTableCardRow>
                <ResponsiveTableCardRow label="PR">
                  <PrBadge hasPr={session.has_pr} />
                </ResponsiveTableCardRow>
                <ResponsiveTableCardRow label="Started">
                  <HelpTip
                    label={new Date(session.started_at).toLocaleString()}
                  >
                    <span className="w-fit">
                      {formatDistanceToNow(new Date(session.started_at), {
                        addSuffix: true,
                      })}
                    </span>
                  </HelpTip>
                </ResponsiveTableCardRow>
              </div>
            </ResponsiveTableCard>
          ))}
        </ResponsiveTableCardList>
      }
    />
  );
}
