"use client";

import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ScrollArea } from "@/components/ui/scroll-area";
import { getAgentDisplayName } from "@/lib/agent-utils";
import type { AdminConversationSummary } from "@/lib/api/a2a";
import { usePulseFlash } from "@/hooks/use-pulse-flash";
import { cn } from "@/lib/utils";
import { formatDistanceToNow } from "date-fns";
import { ListTodo, MessagesSquare } from "lucide-react";
import { PairAvatar } from "./a2a-pair-card";
import { PAIR_PULSE_FADE_MS, pairKey } from "./a2a-switchboard-utils";

interface A2AConversationListProps {
  conversations: AdminConversationSummary[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  isLoading: boolean;
  /** pairKey(agent_a, agent_b) -> epoch ms of the latest matching frame —
   * the same map the switchboard uses, so a row flashes on the same live
   * pulse as its pair's card. */
  pulses: Record<string, number>;
}

interface ConversationRowProps {
  conversation: AdminConversationSummary;
  isSelected: boolean;
  onSelect: (id: string) => void;
  pulsedAt: number | null;
}

function ConversationRow({
  conversation,
  isSelected,
  onSelect,
  pulsedAt,
}: ConversationRowProps) {
  const isPulsing = usePulseFlash(pulsedAt);

  return (
    <div
      role="button"
      tabIndex={0}
      data-testid="conversation-row"
      data-pulsing={isPulsing}
      onClick={() => onSelect(conversation.id)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect(conversation.id);
        }
      }}
      className={cn(
        "block w-full cursor-pointer p-3 rounded-lg border",
        "transition-[background-color,box-shadow] ease-out",
        isSelected ? "border-primary" : "border-border",
        isPulsing
          ? "bg-emerald-500/15 shadow-[0_0_0_1px_rgba(16,185,129,0.6)]"
          : isSelected
            ? "bg-primary/10"
            : "bg-card hover:bg-muted/50 hover:border-primary/50",
      )}
      style={{ transitionDuration: `${PAIR_PULSE_FADE_MS}ms` }}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-start gap-2 min-w-0 flex-1">
          <div className="flex -space-x-2 shrink-0 pt-0.5">
            <PairAvatar slug={conversation.agent_a} />
            <PairAvatar slug={conversation.agent_b} />
          </div>
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
  );
}

export function A2AConversationList({
  conversations,
  selectedId,
  onSelect,
  isLoading,
  pulses,
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
          <ConversationRow
            key={conversation.id}
            conversation={conversation}
            isSelected={selectedId === conversation.id}
            onSelect={onSelect}
            pulsedAt={
              pulses[pairKey(conversation.agent_a, conversation.agent_b)] ??
              null
            }
          />
        ))}
      </div>
    </ScrollArea>
  );
}
