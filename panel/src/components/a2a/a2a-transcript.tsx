"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Markdown } from "@/components/ui/markdown";
import {
  getAgentDisplayName,
  getAgentInitials,
  getAgentTeamColor,
  TEAM_COLOR_CLASSES,
} from "@/lib/agent-utils";
import { cn } from "@/lib/utils";
import type { A2AChatMessage } from "@/lib/api/a2a";
import { formatDistanceToNow } from "date-fns";
import { AlertTriangle, MessagesSquare } from "lucide-react";
import { HelpTip } from "@/components/ui/help-tip";

interface A2ATranscriptProps {
  messages: A2AChatMessage[];
  isLoading: boolean;
  /** False when no conversation/pair is selected at all — distinguishes
   * "nothing to show yet" from "this conversation genuinely has no
   * messages" (design doc §5). Defaults true (existing callers). */
  hasSelection?: boolean;
  /** True when the messages fetch itself failed — a scoped retry, not the
   * page-level OfflineState (design doc §5). */
  error?: boolean;
  onRetry?: () => void;
}

/** How close to the bottom (px) still counts as "at the bottom" for the
 * auto-scroll / new-messages-pill decision. */
const BOTTOM_THRESHOLD_PX = 48;

function EmptyState({
  icon: Icon,
  message,
}: {
  icon: typeof MessagesSquare;
  message: string;
}) {
  return (
    <div className="h-full flex items-center justify-center text-muted-foreground">
      <div className="text-center p-4">
        <Icon className="h-8 w-8 mx-auto mb-2 opacity-50" />
        <p className="text-sm">{message}</p>
      </div>
    </div>
  );
}

export function A2ATranscript({
  messages,
  isLoading,
  hasSelection = true,
  error = false,
  onRetry,
}: A2ATranscriptProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const hasScrolledRef = useRef(false);
  const [isAtBottom, setIsAtBottom] = useState(true);
  const [seenIds, setSeenIds] = useState<Set<string> | null>(null);
  const [newRowIds, setNewRowIds] = useState<ReadonlySet<string>>(new Set());
  const [showJumpPill, setShowJumpPill] = useState(false);
  const [pillEntering, setPillEntering] = useState(false);

  // Chronological (oldest first) regardless of payload ordering.
  const sorted = [...messages].sort(
    (a, b) =>
      new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
  );
  const currentIds = sorted.map((m) => m.id);

  // Render-phase derivation (same idiom as A2APairCard's usePulseFlash — state,
  // not a ref, compared against the current props): the first time a batch of
  // ids appears it seeds "seen" without flagging anything new — messages
  // present at initial load must render settled, never animate in. A later id
  // absent from "seen" is a genuine arrival.
  if (seenIds === null) {
    setSeenIds(new Set(currentIds));
  } else {
    const freshIds = currentIds.filter((id) => !seenIds.has(id));
    if (freshIds.length > 0) {
      setSeenIds(new Set([...seenIds, ...freshIds]));
      if (isAtBottom) {
        setNewRowIds((prev) => new Set([...prev, ...freshIds]));
      } else {
        // Scrolled up: the new row is off-screen — surface the "New
        // messages" pill instead of an invisible entrance transition.
        setShowJumpPill(true);
        setPillEntering(true);
      }
    }
  }

  // Settle the entrance transition one paint frame after new rows appear.
  useEffect(() => {
    if (newRowIds.size === 0) return;
    const raf = requestAnimationFrame(() => setNewRowIds(new Set()));
    return () => cancelAnimationFrame(raf);
  }, [newRowIds]);

  useEffect(() => {
    if (!pillEntering) return;
    const raf = requestAnimationFrame(() => setPillEntering(false));
    return () => cancelAnimationFrame(raf);
  }, [pillEntering]);

  // Auto-scroll to bottom only once on initial load.
  useEffect(() => {
    if (scrollRef.current && sorted.length > 0 && !hasScrolledRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
      hasScrolledRef.current = true;
    }
  }, [sorted.length]);

  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const atBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight < BOTTOM_THRESHOLD_PX;
    setIsAtBottom(atBottom);
    if (atBottom) setShowJumpPill(false);
  }, []);

  const scrollToBottom = useCallback(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
    setShowJumpPill(false);
  }, []);

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

  if (error) {
    return (
      <div className="h-full flex items-center justify-center text-muted-foreground">
        <div className="text-center p-4">
          <AlertTriangle className="h-8 w-8 mx-auto mb-2 opacity-50 text-destructive" />
          <p className="text-sm mb-3">Couldn&apos;t load this conversation</p>
          {onRetry && (
            <HelpTip label="Re-fetches this conversation's messages">
              <Button variant="outline" size="sm" onClick={onRetry}>
                Retry
              </Button>
            </HelpTip>
          )}
        </div>
      </div>
    );
  }

  if (!hasSelection) {
    return (
      <EmptyState
        icon={MessagesSquare}
        message="Select a conversation to view messages"
      />
    );
  }

  if (sorted.length === 0) {
    return (
      <EmptyState
        icon={MessagesSquare}
        message="No messages in this conversation yet"
      />
    );
  }

  return (
    <div className="relative h-full">
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="h-full overflow-y-auto p-4"
      >
        <div className="space-y-3">
          {sorted.map((message) => {
            const isNew = newRowIds.has(message.id);
            const teamColor = getAgentTeamColor(message.from_agent);
            return (
              <div
                key={message.id}
                data-testid="transcript-row"
                data-new={isNew}
                className={cn(
                  "flex gap-3 p-3 rounded-lg border hover:bg-muted/30",
                  // ponytail: one shared 200ms transition covers
                  // opacity/transform/background so the reduced-motion
                  // background flash rides the same timing as the
                  // full-motion fade-in instead of a second bespoke duration.
                  "transition-[opacity,transform,background-color] duration-200 ease-out",
                  "motion-reduce:transition-colors motion-reduce:translate-y-0",
                  isNew
                    ? "opacity-0 translate-y-1 bg-muted/50 motion-reduce:opacity-100"
                    : "opacity-100 translate-y-0 bg-card",
                )}
              >
                <div
                  className={cn(
                    "h-9 w-10 rounded-lg flex items-center justify-center shrink-0 border",
                    TEAM_COLOR_CLASSES[teamColor],
                  )}
                >
                  <span className="text-[10px] font-bold tracking-tight">
                    {getAgentInitials(message.from_agent)}
                  </span>
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1.5">
                    <HelpTip label={`${teamColor.replace("_", "/")} team`}>
                      <span className="font-semibold text-sm w-fit">
                        {getAgentDisplayName(message.from_agent)}
                      </span>
                    </HelpTip>
                    {message.message_kind && (
                      <HelpTip label="Type of agent-to-agent message">
                        <Badge variant="outline" className="text-[10px]">
                          {message.message_kind}
                        </Badge>
                      </HelpTip>
                    )}
                    <HelpTip label={new Date(message.created_at).toLocaleString()}>
                      <span className="text-xs text-muted-foreground ml-auto w-fit">
                        {formatDistanceToNow(new Date(message.created_at))} ago
                      </span>
                    </HelpTip>
                  </div>
                  <div className="text-sm prose prose-sm dark:prose-invert max-w-none">
                    <Markdown>{message.content}</Markdown>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
      {showJumpPill && (
        <HelpTip label="Scrolls down to the newest message">
          <button
            type="button"
            onClick={scrollToBottom}
            className={cn(
              "absolute bottom-3 left-1/2 rounded-full bg-primary text-primary-foreground text-xs px-3 py-1 shadow-md",
              "transition-[opacity,transform] duration-200 ease-out motion-reduce:transition-none",
              pillEntering
                ? "opacity-0 translate-x-[-50%] translate-y-1 motion-reduce:opacity-100 motion-reduce:translate-y-0"
                : "opacity-100 translate-x-[-50%] translate-y-0",
            )}
          >
            New messages ↓
          </button>
        </HelpTip>
      )}
    </div>
  );
}
