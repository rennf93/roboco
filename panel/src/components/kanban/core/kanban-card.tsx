"use client";

import { useState } from "react";
import { Task, TaskStatus } from "@/types";
import { useUpdateTask } from "@/hooks/use-tasks";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { PriorityIndicator } from "../shared/priority-indicator";
import { BlockedBadge } from "../shared/blocked-badge";
import { AssigneeAvatar } from "../shared/assignee-avatar";
import { AgentSelector } from "@/components/agents/agent-selector";
import { TaskTypeBadge } from "@/components/tasks/task-type-badge";
import {
  GripVertical,
  ArrowRight,
  CheckCircle,
  XCircle,
  UserPlus,
  Clock,
  Hash,
} from "lucide-react";
import { toast } from "sonner";
import Link from "next/link";
import { useDraggable } from "@dnd-kit/core";
import { CSS } from "@dnd-kit/utilities";

interface KanbanCardProps {
  task: Task;
  onAction?: (action: string, taskId: string) => void;
  showQaActions?: boolean;
  isDragging?: boolean;
}

export function KanbanCard({
  task,
  onAction,
  showQaActions,
  isDragging: isDraggingProp,
}: KanbanCardProps) {
  const isBlocked = task.status === TaskStatus.BLOCKED;
  const isBacklog = task.status === TaskStatus.BACKLOG;
  const [assignOpen, setAssignOpen] = useState(false);
  const updateTask = useUpdateTask();

  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    isDragging: isDraggingDnd,
  } = useDraggable({
    id: task.id,
    disabled: isBacklog,
  });

  const isDragging = isDraggingProp || isDraggingDnd;

  const dragHandleLabel = "Drag to move task between columns";
  const moveForwardLabel = isBacklog
    ? "PM must activate this task first"
    : "Move forward";

  const style = transform
    ? {
        transform: CSS.Translate.toString(transform),
        zIndex: isDragging ? 50 : undefined,
      }
    : undefined;

  const handleAssign = async (agentId: string | null) => {
    try {
      await updateTask.mutateAsync({
        taskId: task.id,
        updates: { assigned_to: agentId },
      });
      toast.success(agentId ? "Task assigned" : "Task unassigned");
      setAssignOpen(false);
    } catch {
      toast.error("Failed to assign task");
    }
  };

  return (
    <Card
      ref={setNodeRef}
      style={style}
      className={`mb-2 hover:shadow-md transition-all ${
        isDragging ? "opacity-50 rotate-2 scale-105 shadow-lg" : ""
      } ${isBlocked ? "border-red-300 bg-red-50/50 dark:bg-red-950/20" : ""} ${
        isBacklog
          ? "border-slate-300 bg-slate-50/50 dark:bg-slate-950/20 opacity-80"
          : ""
      }`}
    >
      <CardContent className="p-3">
        <div className="flex items-start gap-2">
          <div className="flex-1 min-w-0 overflow-hidden">
            <Link href={"/tasks/" + task.id} className="block" prefetch={false}>
              <p className="font-medium text-sm line-clamp-2 hover:underline break-words">
                <span
                  className="font-mono text-muted-foreground"
                  title={task.id}
                >
                  #{task.id.slice(0, 8)}
                </span>{" "}
                {task.title}
              </p>
            </Link>
            {task.description && (
              <p className="text-xs text-muted-foreground mt-1 line-clamp-2 break-words">
                {task.description.replace(/[#*_`>\-\[\]]/g, "").slice(0, 150)}
              </p>
            )}
          </div>
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <div
                  {...attributes}
                  {...listeners}
                  aria-label={dragHandleLabel}
                  title={dragHandleLabel}
                  className={`shrink-0 mt-0.5 ${isBacklog ? "cursor-not-allowed opacity-50" : "cursor-grab active:cursor-grabbing"}`}
                >
                  <GripVertical className="h-4 w-4 text-muted-foreground" />
                </div>
              </TooltipTrigger>
              <TooltipContent>{dragHandleLabel}</TooltipContent>
            </Tooltip>
          </TooltipProvider>
        </div>

        <div className="flex items-center justify-between mt-2">
          <div className="flex items-center gap-1 flex-wrap">
            <Badge variant="outline" className="text-xs">
              {task.team.replace(/_/g, " ")}
            </Badge>
            <TaskTypeBadge type={task.task_type} showLabel={false} />
            {task.sequence != null && (
              <Tooltip>
                <TooltipTrigger>
                  <Badge variant="outline" className="text-xs gap-1">
                    <Hash className="h-3 w-3" />
                    {task.sequence}
                  </Badge>
                </TooltipTrigger>
                <TooltipContent>
                  <p>
                    Sequence #{task.sequence} — lower-sequence siblings run
                    first
                  </p>
                </TooltipContent>
              </Tooltip>
            )}
            <PriorityIndicator priority={task.priority} />
            {isBlocked && <BlockedBadge />}
            {isBacklog && (
              <Tooltip>
                <TooltipTrigger>
                  <Badge variant="secondary" className="text-xs gap-1">
                    <Clock className="h-3 w-3" />
                    Backlog
                  </Badge>
                </TooltipTrigger>
                <TooltipContent>
                  <p>Awaiting session creation by PM</p>
                </TooltipContent>
              </Tooltip>
            )}
          </div>
          <AssigneeAvatar agentId={task.assigned_to} />
        </div>

        {/* Action buttons */}
        {onAction && (
          <div className="flex items-center justify-between gap-1 mt-2 pt-2 border-t">
            {/* Quick Assign */}
            <Popover open={assignOpen} onOpenChange={setAssignOpen}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <PopoverTrigger asChild>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="max-sm:min-h-11 text-muted-foreground hover:text-foreground"
                      onClick={(e) => e.stopPropagation()}
                      disabled={isBacklog}
                    >
                      <UserPlus className="h-3 w-3 mr-1" />
                      Assign
                    </Button>
                  </PopoverTrigger>
                </TooltipTrigger>
                <TooltipContent>
                  Assign this task to an agent on its team
                </TooltipContent>
              </Tooltip>
              <PopoverContent
                className="w-64 p-2"
                onClick={(e) => e.stopPropagation()}
              >
                <div className="space-y-2">
                  <p className="text-sm font-medium">Assign to agent</p>
                  <AgentSelector
                    value={task.assigned_to}
                    onChange={handleAssign}
                    filterByTeam={task.team}
                    placeholder="Select agent..."
                  />
                </div>
              </PopoverContent>
            </Popover>

            <div className="flex items-center gap-1">
              {showQaActions ? (
                <>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="max-sm:min-h-11 text-green-600 hover:text-green-700 hover:bg-green-50"
                        onClick={(e) => {
                          e.stopPropagation();
                          onAction("pass-qa", task.id);
                        }}
                      >
                        <CheckCircle className="h-3 w-3 mr-1" />
                        Pass
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>
                      Pass QA — you&apos;ll be asked for review notes
                    </TooltipContent>
                  </Tooltip>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="max-sm:min-h-11 text-red-600 hover:text-red-700 hover:bg-red-50"
                        onClick={(e) => {
                          e.stopPropagation();
                          onAction("fail-qa", task.id);
                        }}
                      >
                        <XCircle className="h-3 w-3 mr-1" />
                        Fail
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>
                      Fail QA — returns the task to the developer with notes
                    </TooltipContent>
                  </Tooltip>
                </>
              ) : (
                task.status !== TaskStatus.COMPLETED &&
                task.status !== TaskStatus.CANCELLED && (
                  <TooltipProvider>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-11 w-11"
                          onClick={(e) => {
                            e.stopPropagation();
                            onAction("move-forward", task.id);
                          }}
                          disabled={isBacklog}
                          aria-label={moveForwardLabel}
                          title={moveForwardLabel}
                        >
                          <ArrowRight className="h-3 w-3" />
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent>{moveForwardLabel}</TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                )
              )}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
