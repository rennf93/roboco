"use client";

import { Task, TaskStatus } from "@/types";
import { useSubtasks } from "@/hooks/use-tasks";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ListTree, ExternalLink, Plus } from "lucide-react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { getAgentDisplayName } from "@/lib/agent-utils";

interface SubtasksListProps {
  task: Task;
}

// Status colors for badges
const STATUS_COLORS: Record<TaskStatus, string> = {
  [TaskStatus.BACKLOG]: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400",
  [TaskStatus.PENDING]: "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300",
  [TaskStatus.CLAIMED]: "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300",
  [TaskStatus.IN_PROGRESS]: "bg-blue-200 text-blue-800 dark:bg-blue-800 dark:text-blue-200",
  [TaskStatus.BLOCKED]: "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300",
  [TaskStatus.PAUSED]: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300",
  [TaskStatus.VERIFYING]: "bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300",
  [TaskStatus.NEEDS_REVISION]: "bg-orange-100 text-orange-700 dark:bg-orange-900 dark:text-orange-300",
  [TaskStatus.AWAITING_QA]: "bg-yellow-200 text-yellow-800 dark:bg-yellow-800 dark:text-yellow-200",
  [TaskStatus.AWAITING_DOCUMENTATION]: "bg-indigo-100 text-indigo-700 dark:bg-indigo-900 dark:text-indigo-300",
  [TaskStatus.AWAITING_PM_REVIEW]: "bg-orange-200 text-orange-800 dark:bg-orange-800 dark:text-orange-200",
  [TaskStatus.AWAITING_CEO_APPROVAL]: "bg-amber-200 text-amber-800 dark:bg-amber-800 dark:text-amber-200",
  [TaskStatus.COMPLETED]: "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300",
  [TaskStatus.CANCELLED]: "bg-gray-200 text-gray-600 dark:bg-gray-700 dark:text-gray-400",
  [TaskStatus.QUARANTINED]: "bg-pink-100 text-pink-700 dark:bg-pink-900 dark:text-pink-300",
};

export function SubtasksList({ task }: SubtasksListProps) {
  const { data: subtasks = [], isLoading } = useSubtasks(task.id);

  // Calculate completion stats
  const completedCount = subtasks.filter(
    (t) => t.status === TaskStatus.COMPLETED
  ).length;
  const totalCount = subtasks.length;
  const completionPercent = totalCount > 0 ? Math.round((completedCount / totalCount) * 100) : 0;

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg flex items-center gap-2">
            <ListTree className="h-5 w-5" />
            Subtasks
            {totalCount > 0 && (
              <Badge variant="secondary" className="ml-2">
                {completedCount}/{totalCount}
              </Badge>
            )}
          </CardTitle>
          <div className="flex items-center gap-2">
            {totalCount > 0 && (
              <span className="text-sm text-muted-foreground">
                {completionPercent}% complete
              </span>
            )}
            <Link href={`/tasks?parent=${task.id}&team=${task.team}`}>
              <Button size="sm" variant="ghost">
                <Plus className="h-4 w-4 mr-1" />
                Add
              </Button>
            </Link>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : subtasks.length === 0 ? (
          <div className="text-center py-6 text-muted-foreground">
            <ListTree className="h-8 w-8 mx-auto mb-2 opacity-50" />
            <p className="text-sm">No subtasks</p>
            <p className="text-xs mt-1">
              Create subtasks to break down this task into smaller pieces
            </p>
          </div>
        ) : (
          <div className="space-y-2">
            {/* Progress bar */}
            {totalCount > 0 && (
              <div className="mb-4">
                <div className="h-2 bg-muted rounded-full overflow-hidden">
                  <div
                    className="h-full bg-green-500 transition-all"
                    style={{ width: `${completionPercent}%` }}
                  />
                </div>
              </div>
            )}

            {/* Subtask list */}
            {subtasks.map((subtask) => (
              <Link
                key={subtask.id}
                href={`/tasks/${subtask.id}`}
                className="block"
              >
                <div className="flex items-center justify-between p-3 rounded-lg border hover:bg-muted/50 transition-colors">
                  <div className="flex items-center gap-3 min-w-0">
                    <Badge
                      variant="secondary"
                      className={`text-xs shrink-0 ${STATUS_COLORS[subtask.status]}`}
                    >
                      {subtask.status.replace(/_/g, " ")}
                    </Badge>
                    <span className="text-sm truncate">{subtask.title}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    {subtask.assigned_to && (
                      <Badge variant="outline" className="text-xs">
                        {getAgentDisplayName(subtask.assigned_to)}
                      </Badge>
                    )}
                    <ExternalLink className="h-4 w-4 text-muted-foreground" />
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
