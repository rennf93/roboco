"use client";

import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { HelpTip } from "@/components/ui/help-tip";
import { useStalledTasks } from "@/hooks/use-dashboard";
import { AlertOctagon, AlertCircle, Clock } from "lucide-react";

// Formats the backend's own stalled_seconds for display - this is display
// formatting only, it never re-derives what counts as "stalled" (that's the
// backend's durable stalled_reason/stalled_since marker).
function formatStalledDuration(seconds: number): string {
  if (seconds < 0) return "unknown";
  const hours = Math.floor(seconds / 3600);
  if (hours < 1) return "< 1h";
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

/**
 * Overview "Stalled / Needs you" section - every task the dispatcher's
 * respawn breaker has given up on, driven entirely by
 * GET /dashboard/stalled-tasks (useStalledTasks). Title, status, assignee,
 * and reason render verbatim from that response; a failed fetch renders a
 * distinct error state, never the empty state.
 */
export function StalledNeedsYouPanel() {
  const { data: stalledTasks, isLoading, isError } = useStalledTasks();

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <HelpTip label="Tasks the dispatcher's respawn breaker has given up on — a durable stalled marker, oldest-stalled-first">
            <CardTitle className="text-lg flex items-center gap-2">
              <AlertOctagon className="h-5 w-5 text-amber-500" />
              Stalled / Needs You
            </CardTitle>
          </HelpTip>
          {!isLoading && !isError && (stalledTasks?.length ?? 0) > 0 && (
            <Badge variant="destructive">{stalledTasks?.length}</Badge>
          )}
        </div>
      </CardHeader>
      <CardContent>
        {isError ? (
          <div className="flex items-center gap-2 rounded-md border border-destructive/50 bg-destructive/10 px-3 py-4 text-sm text-destructive">
            <AlertCircle className="h-4 w-4 shrink-0" />
            Failed to load stalled tasks.
          </div>
        ) : isLoading ? (
          <div className="space-y-3">
            <Skeleton className="h-16" />
            <Skeleton className="h-16" />
          </div>
        ) : stalledTasks && stalledTasks.length === 0 ? (
          <div className="text-center py-4 text-muted-foreground text-sm">
            <AlertOctagon className="h-8 w-8 mx-auto mb-2 opacity-50" />
            Nothing is stalled right now
          </div>
        ) : (
          <div className="space-y-3">
            {stalledTasks?.map((task) => (
              <Link
                key={task.task_id}
                href={"/tasks/" + task.task_id}
                prefetch={false}
              >
                <div className="flex items-start gap-3 p-3 rounded-lg border border-amber-200 bg-amber-50 hover:bg-amber-100 dark:border-amber-900 dark:bg-amber-950 dark:hover:bg-amber-900 transition-colors">
                  <AlertOctagon className="h-5 w-5 shrink-0 text-amber-600 dark:text-amber-400" />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1 flex-wrap">
                      <span className="font-medium text-sm truncate">
                        {task.title}
                      </span>
                      <Badge variant="outline" className="text-xs">
                        {task.status}
                      </Badge>
                      {(task.assignee_slug || task.assignee_id) && (
                        <span className="text-xs text-muted-foreground">
                          {task.assignee_slug ?? task.assignee_id}
                        </span>
                      )}
                    </div>
                    <p className="text-sm text-muted-foreground truncate">
                      {task.reason}
                    </p>
                    <div className="flex items-center gap-1 mt-1 text-xs text-muted-foreground">
                      <Clock className="h-3 w-3" />
                      Stalled for {formatStalledDuration(task.stalled_seconds)}
                    </div>
                  </div>
                </div>
              </Link>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
