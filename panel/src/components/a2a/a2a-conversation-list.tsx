"use client";

import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ScrollArea } from "@/components/ui/scroll-area";
import { getAgentDisplayName } from "@/lib/agent-utils";
import type { AdminConversationSummary } from "@/lib/api/a2a";
import { formatDistanceToNow } from "date-fns";
import { ListTodo, MessagesSquare } from "lucide-react";

interface A2AConversationListProps {
  conversations: AdminConversationSummary[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  isLoading: boolean;
}

export function A2AConversationList({
  conversations,
  selectedId,
  onSelect,
  isLoading,
}: A2AConversationListProps) {
  if (isLoading) {
    return (
      <div className="p-2 space-y-2">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-20 w-full" />
        ))}
      </div>
    );
  }

  if (conversations.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-muted-foreground">
        <div className="text-center p-4">
          <MessagesSquare className="h-8 w-8 mx-auto mb-2 opacity-50" />
          <p className="text-sm">No A2A conversations yet</p>
        </div>
      </div>
    );
  }

  return (
    <ScrollArea className="h-full">
      <div className="p-2 space-y-2">
        {conversations.map((conversation) => (
          <div
            key={conversation.id}
            role="button"
            tabIndex={0}
            onClick={() => onSelect(conversation.id)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onSelect(conversation.id);
              }
            }}
            className={
              "block w-full cursor-pointer p-3 rounded-lg border transition-all " +
              (selectedId === conversation.id
                ? "bg-primary/10 border-primary"
                : "bg-card hover:bg-muted/50 hover:border-primary/50")
            }
          >
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0 flex-1">
                <div className="font-medium text-sm truncate">
                  {getAgentDisplayName(conversation.agent_a)}
                  {" ↔ "}
                  {getAgentDisplayName(conversation.agent_b)}
                </div>
                {conversation.topic && (
                  <div className="text-xs text-muted-foreground truncate mt-0.5">
                    {conversation.topic}
                  </div>
                )}
                <div className="text-xs text-muted-foreground mt-1">
                  {formatDistanceToNow(
                    new Date(
                      conversation.last_message_at ?? conversation.created_at,
                    ),
                  )}{" "}
                  ago
                </div>
                {conversation.last_message_preview && (
                  <p className="text-xs text-muted-foreground truncate mt-1">
                    {conversation.last_message_preview}
                  </p>
                )}
                {conversation.task_id && (
                  <Link
                    prefetch={false}
                    href={`/tasks/${conversation.task_id}`}
                    onClick={(e) => e.stopPropagation()}
                    className="inline-flex items-center gap-1 text-xs text-primary hover:underline mt-1"
                  >
                    <ListTodo className="h-3 w-3" />
                    Task {conversation.task_id.slice(0, 8)}
                  </Link>
                )}
              </div>
              <div className="flex flex-col items-end gap-1 shrink-0">
                <Badge
                  variant={
                    conversation.status === "active" ? "default" : "secondary"
                  }
                  className="text-xs"
                >
                  {conversation.status}
                </Badge>
                <span className="text-xs text-muted-foreground">
                  {conversation.message_count} msgs
                </span>
              </div>
            </div>
          </div>
        ))}
      </div>
    </ScrollArea>
  );
}
