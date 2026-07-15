"use client";

import { Task, TaskStatus } from "@/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { AlertTriangle, Clock, ArrowRight } from "lucide-react";
import Link from "next/link";
import { HelpTip } from "@/components/ui/help-tip";

interface ActiveBlockersPanelProps {
  tasks: Task[] | undefined;
  isLoading: boolean;
}

function formatDuration(date: string): string {
  const start = new Date(date);
  const now = new Date();
  const diffMs = now.getTime() - start.getTime();
  const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
  const diffDays = Math.floor(diffHours / 24);

  if (diffHours < 1) return "< 1h";
  if (diffHours < 24) return `${diffHours}h`;
  return `${diffDays}d`;
}

export function ActiveBlockersPanel({
  tasks,
  isLoading,
}: ActiveBlockersPanelProps) {
  // Filter blocked tasks and sort by how long they've been blocked
  const blockedTasks = (tasks ?? [])
    .filter((t) => t.status === TaskStatus.BLOCKED)
    .sort((a, b) => {
      const aTime = a.updated_at ? new Date(a.updated_at).getTime() : 0;
      const bTime = b.updated_at ? new Date(b.updated_at).getTime() : 0;
      return aTime - bTime; // Oldest first
    })
    .slice(0, 5);

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <HelpTip label="Tasks in BLOCKED status, oldest first">
            <CardTitle className="text-lg flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-red-500" />
              Active Blockers
            </CardTitle>
          </HelpTip>
          {blockedTasks.length > 0 && (
            <HelpTip label="Shown below — capped at 5, there may be more">
              <Badge variant="destructive">{blockedTasks.length}</Badge>
            </HelpTip>
          )}
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-3">
            <Skeleton className="h-16" />
            <Skeleton className="h-16" />
          </div>
        ) : blockedTasks.length === 0 ? (
          <div className="text-center py-4 text-muted-foreground text-sm">
            <AlertTriangle className="h-8 w-8 mx-auto mb-2 opacity-50" />
            No blocked tasks
          </div>
        ) : (
          <div className="space-y-3">
            {blockedTasks.map((task) => (
              <Link key={task.id} href={"/tasks/" + task.id} prefetch={false}>
                <div className="flex items-start gap-3 p-3 rounded-lg border border-red-200 bg-red-50 hover:bg-red-100 dark:border-red-900 dark:bg-red-950 dark:hover:bg-red-900 transition-colors">
                  <span className="text-lg">\uD83D\uDD34</span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <HelpTip label="Short task ID — first 8 characters of the full task identifier">
                        <span className="font-medium text-sm truncate">
                          Task #{task.id.slice(0, 8)}
                        </span>
                      </HelpTip>
                      <HelpTip label="Team currently responsible for this task">
                        <Badge variant="outline" className="text-xs capitalize">
                          {task.team.replace(/_/g, " ")}
                        </Badge>
                      </HelpTip>
                    </div>
                    <p className="text-sm truncate">{task.title}</p>
                    <HelpTip label="Time since this task's last status update (or creation, if never updated)">
                      <div className="flex items-center gap-1 mt-1 text-xs text-muted-foreground">
                        <Clock className="h-3 w-3" />
                        Blocked for{" "}
                        {formatDuration(task.updated_at ?? task.created_at)}
                      </div>
                    </HelpTip>
                  </div>
                </div>
              </Link>
            ))}
          </div>
        )}
        <div className="mt-4 pt-3 border-t">
          <Link href="/tasks?status=blocked" prefetch={false}>
            <HelpTip label="The complete blocked-task list — not capped to 5">
              <Button variant="ghost" size="sm" className="w-full">
                View All Blocked
                <ArrowRight className="h-4 w-4 ml-2" />
              </Button>
            </HelpTip>
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}
