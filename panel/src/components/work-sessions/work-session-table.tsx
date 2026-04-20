"use client";

import { formatDistanceToNow } from "date-fns";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
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

export function WorkSessionTable({ sessions, isLoading }: WorkSessionTableProps) {
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
        <p className="text-sm">Work sessions are created when agents start working on tasks</p>
      </div>
    );
  }

  return (
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
                  <Link
                    href={`/work-sessions/${session.id}`}
                    className="font-medium hover:underline font-mono text-sm"
                  >
                    {session.branch_name}
                  </Link>
                </div>
              </TableCell>
              <TableCell>
                <Link
                  href={`/tasks/${session.task_id}`}
                  className="text-sm text-muted-foreground hover:text-foreground hover:underline"
                >
                  {session.task_id.slice(0, 8)}...
                </Link>
              </TableCell>
              <TableCell>{getStatusBadge(session.status)}</TableCell>
              <TableCell>
                {session.has_pr ? (
                  <Badge className="bg-purple-500/10 text-purple-500">
                    <GitPullRequest className="h-3 w-3 mr-1" />
                    PR Open
                  </Badge>
                ) : (
                  <span className="text-sm text-muted-foreground">No PR</span>
                )}
              </TableCell>
              <TableCell className="text-sm text-muted-foreground">
                {formatDistanceToNow(new Date(session.started_at), { addSuffix: true })}
              </TableCell>
              <TableCell>
                <Link href={`/work-sessions/${session.id}`}>
                  <Button variant="ghost" size="icon">
                    <ExternalLink className="h-4 w-4" />
                  </Button>
                </Link>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
