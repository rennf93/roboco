"use client";

import { memo, useRef } from "react";
import { Task, TaskStatus } from "@/types";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { HelpTip } from "@/components/ui/help-tip";
import { taskStatusDescription } from "@/components/tasks/task-status-badge";
import { KanbanCard } from "./kanban-card";
import { useDroppable } from "@dnd-kit/core";
import { useVirtualizer } from "@tanstack/react-virtual";
import { cn } from "@/lib/utils";

// Rough collapsed-card height (title + badges + assign row) used as the
// virtualizer's initial estimate — corrected per-card via measureElement
// once mounted, so a wrong guess only costs one extra frame of scroll jitter.
const ESTIMATED_CARD_HEIGHT = 132;

interface KanbanColumnProps {
  id: string;
  title: string;
  status: TaskStatus;
  tasks: Task[];
  color: string;
  isLoading: boolean;
  onAction?: (action: string, taskId: string) => void;
  showQaActions?: boolean;
  /** Extra Tailwind classes forwarded to the root element (e.g. w-full for mobile). */
  className?: string;
}

// Memoized: KanbanBoard renders one of these per lifecycle-status column, and
// a column's `tasks` slice is stabilized upstream (kanban-board.tsx's
// tasksByStatus useMemo) so an unrelated board re-render (dialogs, drag
// state) skips every column that didn't actually change.
function KanbanColumnImpl({
  id: _id,
  title,
  status,
  tasks,
  color,
  isLoading,
  onAction,
  showQaActions,
  className,
}: KanbanColumnProps) {
  void _id; // Reserved for future use
  const { setNodeRef, isOver } = useDroppable({
    id: status,
  });
  const scrollRef = useRef<HTMLDivElement>(null);
  // A column can carry hundreds of cards (every task in a status, across a
  // whole team's history) — window the DOM to roughly what's on screen
  // instead of mounting every dnd-kit draggable card at once. Safe with
  // dnd-kit here because the drop target is the column itself
  // (useDroppable above), not per-card — cards only register useDraggable,
  // no sortable reordering depends on every card's DOM node existing.
  const rowVirtualizer = useVirtualizer({
    count: tasks.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ESTIMATED_CARD_HEIGHT,
    overscan: 6,
    getItemKey: (index) => tasks[index]?.id ?? index,
  });

  return (
    <div
      ref={setNodeRef}
      className={cn(
        // Columns share the available width (flex-1) down to a 18rem floor —
        // below that the board container scrolls horizontally — and cap at
        // 24rem so a near-empty board doesn't produce comically wide columns.
        "flex min-w-0 flex-col rounded-lg p-3 h-full shrink-0 w-72 sm:w-auto sm:flex-1 sm:min-w-72 sm:max-w-96",
        color,
        isOver && "ring-2 ring-primary ring-offset-2",
        className,
      )}
    >
      <div className="flex items-center justify-between mb-3">
        {/* Reuses the canonical per-status copy (task-status-badge.tsx) so a
            column header explains what a drag-drop here actually does. */}
        <HelpTip
          label={
            taskStatusDescription(status)
              ? `Dropping a card here fires this transition: ${taskStatusDescription(status)}`
              : null
          }
        >
          <h3 className="font-semibold text-sm text-gray-800 dark:text-gray-100 w-fit">
            {title}
          </h3>
        </HelpTip>
        <Tooltip>
          <TooltipTrigger asChild>
            <Badge
              variant="secondary"
              className="dark:bg-gray-700 dark:text-gray-100"
            >
              {isLoading ? "..." : tasks.length}
            </Badge>
          </TooltipTrigger>
          <TooltipContent>
            {isLoading
              ? "Loading…"
              : `${tasks.length} task${tasks.length === 1 ? "" : "s"} in ${title}`}
          </TooltipContent>
        </Tooltip>
      </div>
      {/* Native overflow scroll: Radix ScrollArea's display:table viewport
          let cards grow past the column width and clip — a plain div keeps
          content constrained to the column. Also the virtualizer's scroll
          container. */}
      <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto pr-2">
        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-24" />
            <Skeleton className="h-24" />
            <Skeleton className="h-24" />
          </div>
        ) : tasks.length === 0 ? (
          <div className="text-center py-8 text-muted-foreground text-sm">
            No tasks
          </div>
        ) : (
          <div
            className="relative w-full"
            style={{ height: rowVirtualizer.getTotalSize() }}
          >
            {rowVirtualizer.getVirtualItems().map((virtualRow) => {
              const task = tasks[virtualRow.index];
              return (
                <div
                  key={virtualRow.key}
                  data-index={virtualRow.index}
                  ref={rowVirtualizer.measureElement}
                  className="absolute top-0 left-0 w-full"
                  style={{ transform: `translateY(${virtualRow.start}px)` }}
                >
                  <KanbanCard
                    task={task}
                    onAction={onAction}
                    showQaActions={
                      showQaActions &&
                      (status === TaskStatus.AWAITING_QA ||
                        status === TaskStatus.VERIFYING)
                    }
                  />
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

export const KanbanColumn = memo(KanbanColumnImpl);
