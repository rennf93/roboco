"use client";

import { Task } from "@/types";
import { TaskDescription } from "./task-description";
import { AcceptanceCriteria } from "./acceptance-criteria";
import { SubtasksList } from "./subtasks-list";
import { WorkSessionCard } from "./work-session-card";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Markdown } from "@/components/ui/markdown";
import Link from "next/link";

interface TabOverviewProps {
  task: Task;
}

export function TabOverview({ task }: TabOverviewProps) {
  return (
    <div className="space-y-6">
      {/* Parent Task Link */}
      {task.parent_task_id && (
        <Card className="bg-muted/30">
          <CardContent className="py-3">
            <div className="flex items-center gap-2 text-sm">
              <span className="text-muted-foreground">Subtask of:</span>
              <Link
                href={`/tasks/${task.parent_task_id}`}
                className="text-primary hover:underline font-medium"
              >
                Parent Task #{task.parent_task_id.slice(0, 8)}
              </Link>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Description */}
      <TaskDescription task={task} />

      {/* Acceptance Criteria */}
      <AcceptanceCriteria task={task} />

      {/* Subtasks */}
      <SubtasksList task={task} />

      {/* Work Session / Git Info */}
      <WorkSessionCard taskId={task.id} />

      {/* Quick Context (for resumption) */}
      {task.quick_context && (
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Quick Context</CardTitle>
          </CardHeader>
          <CardContent>
            <Markdown className="text-sm">{task.quick_context}</Markdown>
          </CardContent>
        </Card>
      )}

      {/* Verification Status */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Verification Status</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted-foreground">Self Verified:</span>
              <Badge variant={task.self_verified ? "default" : "secondary"}>
                {task.self_verified ? "Yes" : "No"}
              </Badge>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted-foreground">QA Verified:</span>
              <Badge
                variant={
                  task.qa_verified === true
                    ? "default"
                    : task.qa_verified === false
                    ? "destructive"
                    : "secondary"
                }
              >
                {task.qa_verified === true
                  ? "Passed"
                  : task.qa_verified === false
                  ? "Failed"
                  : "Pending"}
              </Badge>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
