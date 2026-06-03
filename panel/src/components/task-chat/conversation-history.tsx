"use client";

import { MessageSquare, ArrowRight, RotateCcw, Clock } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { TaskStatus } from "@/types";
import { cn } from "@/lib/utils";

/**
 * A lightweight representation of a past chat-based task creation session.
 * Intentionally separate from the full Task interface so this component
 * can be used without a live API connection (e.g. during prototyping).
 */
export interface ConversationHistoryItem {
  id: string;
  taskTitle: string;
  status: TaskStatus;
  /** ISO timestamp of the last message in this conversation */
  lastActivityAt: string;
  /** Number of chat messages exchanged */
  messageCount: number;
}

/**
 * Status → human-readable label mapping.
 * Avoids raw ISO timestamps in the list (acceptance criterion).
 */
const STATUS_LABEL: Record<TaskStatus, string> = {
  [TaskStatus.BACKLOG]: "Backlog",
  [TaskStatus.PENDING]: "Pending",
  [TaskStatus.CLAIMED]: "Claimed",
  [TaskStatus.IN_PROGRESS]: "In Progress",
  [TaskStatus.BLOCKED]: "Blocked",
  [TaskStatus.PAUSED]: "Paused",
  [TaskStatus.VERIFYING]: "Verifying",
  [TaskStatus.NEEDS_REVISION]: "Needs Revision",
  [TaskStatus.AWAITING_QA]: "Awaiting QA",
  [TaskStatus.AWAITING_DOCUMENTATION]: "Awaiting Docs",
  [TaskStatus.AWAITING_PM_REVIEW]: "Awaiting PM",
  [TaskStatus.AWAITING_CEO_APPROVAL]: "Awaiting CEO",
  [TaskStatus.COMPLETED]: "Completed",
  [TaskStatus.CANCELLED]: "Cancelled",
};

/** Tailwind classes per status for the badge color — reuses existing token palette */
const STATUS_BADGE_CLASS: Record<TaskStatus, string> = {
  [TaskStatus.BACKLOG]: "bg-slate-500",
  [TaskStatus.PENDING]: "bg-gray-500",
  [TaskStatus.CLAIMED]: "bg-blue-400",
  [TaskStatus.IN_PROGRESS]: "bg-blue-600",
  [TaskStatus.BLOCKED]: "bg-red-500",
  [TaskStatus.PAUSED]: "bg-yellow-500",
  [TaskStatus.VERIFYING]: "bg-purple-500",
  [TaskStatus.NEEDS_REVISION]: "bg-orange-500",
  [TaskStatus.AWAITING_QA]: "bg-yellow-600",
  [TaskStatus.AWAITING_DOCUMENTATION]: "bg-indigo-500",
  [TaskStatus.AWAITING_PM_REVIEW]: "bg-orange-600",
  [TaskStatus.AWAITING_CEO_APPROVAL]: "bg-amber-600",
  [TaskStatus.COMPLETED]: "bg-green-500",
  [TaskStatus.CANCELLED]: "bg-gray-400",
};

/**
 * Format relative time without an external library.
 * Returns strings like "2 minutes ago", "yesterday", "Jun 1".
 */
function formatRelativeTime(isoTimestamp: string): string {
  const date = new Date(isoTimestamp);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60_000);
  const diffHours = Math.floor(diffMs / 3_600_000);
  const diffDays = Math.floor(diffMs / 86_400_000);

  if (diffMins < 1) return "just now";
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays === 1) return "yesterday";
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

interface ConversationHistoryItemCardProps {
  item: ConversationHistoryItem;
  isSelected: boolean;
  onContinue: (id: string) => void;
  onReuse: (id: string) => void;
}

function ConversationHistoryItemCard({
  item,
  isSelected,
  onContinue,
  onReuse,
}: ConversationHistoryItemCardProps) {
  const isCompleted =
    item.status === TaskStatus.COMPLETED || item.status === TaskStatus.CANCELLED;

  return (
    <div
      className={cn(
        "rounded-lg border p-3 transition-colors cursor-pointer",
        isSelected
          ? "border-primary bg-primary/5"
          : "border-border hover:border-primary/50 hover:bg-muted/50"
      )}
      role="listitem"
    >
      {/* Task title */}
      <p className="text-sm font-medium leading-tight line-clamp-2 mb-2">
        {item.taskTitle}
      </p>

      {/* Status badge — task title + status badge per item (acceptance criterion) */}
      <div className="flex items-center gap-2 mb-3">
        <Badge
          className={cn(STATUS_BADGE_CLASS[item.status], "text-white text-xs")}
        >
          {STATUS_LABEL[item.status]}
        </Badge>
        <span className="flex items-center gap-1 text-xs text-muted-foreground ml-auto">
          <Clock className="h-3 w-3" aria-hidden="true" />
          {formatRelativeTime(item.lastActivityAt)}
        </span>
      </div>

      {/*
       * AFFORDANCE — selecting an item provides a clear affordance to continue
       * or reuse the conversation (acceptance criterion).
       *
       * "Continue" resumes the active conversation in the chat panel.
       * "Reuse" opens a fresh chat pre-seeded with this draft's structure.
       * Both are shown as distinct labeled buttons — not icon-only — for clarity.
       */}
      <div className="flex items-center gap-1.5">
        {!isCompleted && (
          <Button
            variant="default"
            size="sm"
            className="flex-1 h-7 text-xs gap-1"
            onClick={() => onContinue(item.id)}
            aria-label={`Continue conversation for ${item.taskTitle}`}
          >
            <ArrowRight className="h-3 w-3" aria-hidden="true" />
            Continue
          </Button>
        )}
        <Button
          variant="outline"
          size="sm"
          className={cn("h-7 text-xs gap-1", !isCompleted ? "flex-none" : "flex-1")}
          onClick={() => onReuse(item.id)}
          aria-label={`Reuse draft structure from ${item.taskTitle}`}
        >
          <RotateCcw className="h-3 w-3" aria-hidden="true" />
          Reuse
        </Button>
      </div>
    </div>
  );
}

interface ConversationHistoryProps {
  items: ConversationHistoryItem[];
  selectedId: string | null;
  onContinue: (id: string) => void;
  onReuse: (id: string) => void;
  onNewConversation: () => void;
}

export function ConversationHistory({
  items,
  selectedId,
  onContinue,
  onReuse,
  onNewConversation,
}: ConversationHistoryProps) {
  return (
    <Card className="h-full flex flex-col gap-0 py-0 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b shrink-0">
        <div className="flex items-center gap-2">
          <MessageSquare className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
          <span className="text-sm font-medium">History</span>
          {items.length > 0 && (
            <Badge variant="secondary" className="text-xs h-5 px-1.5">
              {items.length}
            </Badge>
          )}
        </div>
        <Button
          variant="ghost"
          size="sm"
          className="h-7 text-xs"
          onClick={onNewConversation}
          aria-label="Start a new task conversation"
        >
          + New
        </Button>
      </div>

      {/* Conversation list */}
      <ScrollArea className="flex-1">
        <div className="p-3 space-y-2" role="list" aria-label="Conversation history">
          {items.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              <MessageSquare className="h-8 w-8 mx-auto mb-2 opacity-30" aria-hidden="true" />
              <p className="text-xs">No conversations yet</p>
              <p className="text-xs mt-1">Start a chat to create your first task</p>
            </div>
          ) : (
            items.map((item, index) => (
              <div key={item.id}>
                <ConversationHistoryItemCard
                  item={item}
                  isSelected={selectedId === item.id}
                  onContinue={onContinue}
                  onReuse={onReuse}
                />
                {index < items.length - 1 && (
                  <Separator className="mt-2" />
                )}
              </div>
            ))
          )}
        </div>
      </ScrollArea>
    </Card>
  );
}
