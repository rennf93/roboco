"use client";

import { Task } from "@/types";
import { TaskDescription } from "./task-description";
import { AcceptanceCriteria } from "./acceptance-criteria";
import { SubtasksList } from "./subtasks-list";
import { WorkSessionCard } from "./work-session-card";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Markdown } from "@/components/ui/markdown";
import { HelpTip } from "@/components/ui/help-tip";
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
              <HelpTip label={task.parent_task_id}>
                <Link
                  prefetch={false}
                  href={`/tasks/${task.parent_task_id}`}
                  className="text-primary hover:underline font-medium"
                >
                  Parent Task #{task.parent_task_id.slice(0, 8)}
                </Link>
              </HelpTip>
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
            <HelpTip label="Short notes for resuming work after an interruption — mirrored on the Notes tab">
              <CardTitle className="text-lg w-fit">Quick Context</CardTitle>
            </HelpTip>
          </CardHeader>
          <CardContent>
            <Markdown className="text-sm">{task.quick_context}</Markdown>
          </CardContent>
        </Card>
      )}

      {/* Verification Status */}
      <Card>
        <CardHeader>
          <HelpTip label="Two independent checks: the developer's self-verification, then QA's separate review">
            <CardTitle className="text-lg w-fit">Verification Status</CardTitle>
          </HelpTip>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted-foreground">
                Self Verified:
              </span>
              <HelpTip label="The developer confirmed they tested their own work before requesting QA review">
                <Badge variant={task.self_verified ? "default" : "secondary"}>
                  {task.self_verified ? "Yes" : "No"}
                </Badge>
              </HelpTip>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted-foreground">
                QA Verified:
              </span>
              <HelpTip label="Whether QA reviewed this task: Passed, Failed (back to needs_revision), or Pending (not reviewed yet)">
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
              </HelpTip>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
