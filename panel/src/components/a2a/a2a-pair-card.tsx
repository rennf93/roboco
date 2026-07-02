"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { getAgentDisplayName, getAgentInitials } from "@/lib/agent-utils";
import type { AdminPairSummary } from "@/lib/api/a2a";
import { cn } from "@/lib/utils";
import { formatDistanceToNow } from "date-fns";
import { PAIR_PULSE_FADE_MS } from "./a2a-switchboard-utils";

interface A2APairCardProps {
  pair: AdminPairSummary;
  /** Epoch ms of the latest a2a.message frame matching this pair, or null
   * when neither side has ever A2A'd the other (this session). */
  pulsedAt: number | null;
  isSelected?: boolean;
  onOpen: () => void;
}

function PairAvatar({ slug }: { slug: string }) {
  return (
    <div
      className="h-7 w-7 rounded-full bg-primary/10 border flex items-center justify-center shrink-0"
      title={getAgentDisplayName(slug)}
    >
      <span className="text-[9px] font-bold tracking-tight">
        {getAgentInitials(slug)}
      </span>
    </div>
  );
}

/**
 * One pair card in the A2A switchboard. Lights up on a matching a2a.message
 * frame — the card jumps to full "hot" intensity, then a plain CSS
 * transition (no animation library) fades it back to baseline over
 * PAIR_PULSE_FADE_MS. A never-talked pair (no conversation_id) renders
 * dimmed/compact.
 */
export function A2APairCard({
  pair,
  pulsedAt,
  isSelected,
  onOpen,
}: A2APairCardProps) {
  const hasHistory = pair.conversation_id !== null;
  const [isPulsing, setIsPulsing] = useState(false);

  // Render-phase derivation, not an Effect (react.dev/learn/you-might-not-
  // need-an-effect#adjusting-some-state-when-a-prop-changes): flash hot in
  // the very same render that receives a new pulsedAt, comparing against the
  // last value we've seen. No cascading extra render from an Effect body.
  // Seeded to null (not the initial pulsedAt) so a card that *mounts*
  // already carrying a live pulse — e.g. switching into switchboard view
  // right after a frame arrived — still flashes hot instead of looking cold.
  const [lastSeenPulse, setLastSeenPulse] = useState<number | null>(null);
  if (pulsedAt !== lastSeenPulse) {
    setLastSeenPulse(pulsedAt);
    if (pulsedAt !== null) setIsPulsing(true);
  }

  // Flip back on the next paint frame — the long CSS transition-duration
  // below then animates the decay from "hot" to baseline over
  // PAIR_PULSE_FADE_MS. The setState here is inside the (async) rAF
  // callback, not the Effect body itself, so it's the intended "subscribe to
  // an external clock" use of an Effect.
  useEffect(() => {
    if (!isPulsing) return;
    const raf = requestAnimationFrame(() => setIsPulsing(false));
    return () => cancelAnimationFrame(raf);
  }, [isPulsing]);

  return (
    <button
      type="button"
      onClick={onOpen}
      data-testid="pair-card"
      data-pulsing={isPulsing}
      data-group={pair.group_key}
      aria-pressed={!!isSelected}
      className={cn(
        "w-full text-left rounded-lg border p-2.5 cursor-pointer",
        "transition-[background-color,box-shadow] ease-out",
        isSelected ? "border-primary" : "border-border",
        !hasHistory && "opacity-60",
        isPulsing
          ? "bg-emerald-500/15 shadow-[0_0_0_1px_rgba(16,185,129,0.6)]"
          : "bg-card hover:bg-muted/50",
      )}
      style={{ transitionDuration: `${PAIR_PULSE_FADE_MS}ms` }}
    >
      <div className="flex items-center gap-2">
        <div className="flex -space-x-2">
          <PairAvatar slug={pair.agent_a} />
          <PairAvatar slug={pair.agent_b} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium truncate">
            {getAgentDisplayName(pair.agent_a)}
            {" ↔ "}
            {getAgentDisplayName(pair.agent_b)}
          </div>
          <div className="text-xs text-muted-foreground">
            {hasHistory && pair.last_message_at
              ? `${formatDistanceToNow(new Date(pair.last_message_at))} ago`
              : "No A2A yet"}
          </div>
        </div>
        {hasHistory && (
          <Badge variant="secondary" className="text-[10px] shrink-0">
            {pair.message_count}
          </Badge>
        )}
      </div>
    </button>
  );
}
