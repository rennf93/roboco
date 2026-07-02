"use client";

import { useEffect, useRef } from "react";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Markdown } from "@/components/ui/markdown";
import { getAgentDisplayName, getAgentInitials } from "@/lib/agent-utils";
import type { A2AChatMessage } from "@/lib/api/a2a";
import { formatDistanceToNow } from "date-fns";
import { MessagesSquare } from "lucide-react";

interface A2ATranscriptProps {
  messages: A2AChatMessage[];
  isLoading: boolean;
}

export function A2ATranscript({ messages, isLoading }: A2ATranscriptProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const hasScrolledRef = useRef(false);

  // Chronological (oldest first) regardless of payload ordering.
  const sorted = [...messages].sort(
    (a, b) =>
      new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
  );

  // Auto-scroll to bottom only once on initial load.
  useEffect(() => {
    if (scrollRef.current && sorted.length > 0 && !hasScrolledRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
      hasScrolledRef.current = true;
    }
  }, [sorted.length]);

  if (isLoading) {
    return (
      <div className="p-4 space-y-4">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="flex gap-3">
            <Skeleton className="h-8 w-8 rounded-full" />
            <div className="flex-1">
              <Skeleton className="h-4 w-32 mb-2" />
              <Skeleton className="h-12 w-full" />
            </div>
          </div>
        ))}
      </div>
    );
  }

  if (sorted.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-muted-foreground">
        <div className="text-center p-4">
          <MessagesSquare className="h-8 w-8 mx-auto mb-2 opacity-50" />
          <p className="text-sm">No messages in this conversation yet</p>
        </div>
      </div>
    );
  }

  return (
    <div ref={scrollRef} className="h-full overflow-y-auto p-4">
      <div className="space-y-3">
        {sorted.map((message) => (
          <div
            key={message.id}
            className="flex gap-3 p-3 rounded-lg border bg-card hover:bg-muted/30 transition-colors"
          >
            <div className="h-9 w-10 rounded-lg bg-primary/10 flex items-center justify-center shrink-0 border">
              <span className="text-[10px] font-bold tracking-tight">
                {getAgentInitials(message.from_agent)}
              </span>
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-1.5">
                <span className="font-semibold text-sm">
                  {getAgentDisplayName(message.from_agent)}
                </span>
                {message.message_kind && (
                  <Badge variant="outline" className="text-[10px]">
                    {message.message_kind}
                  </Badge>
                )}
                <span className="text-xs text-muted-foreground ml-auto">
                  {formatDistanceToNow(new Date(message.created_at))} ago
                </span>
              </div>
              <div className="text-sm prose prose-sm dark:prose-invert max-w-none">
                <Markdown>{message.content}</Markdown>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
