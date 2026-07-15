"use client";

import { Badge } from "@/components/ui/badge";
import { HelpTip } from "@/components/ui/help-tip";
import {
  getAgentDisplayName,
  getAgentInitials,
  getAgentTeamColor,
  TEAM_COLOR_CLASSES,
} from "@/lib/agent-utils";
import type { AdminPairSummary } from "@/lib/api/a2a";
import { cn } from "@/lib/utils";
import { usePulseFlash } from "@/hooks/use-pulse-flash";
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

/** One agent's avatar (initials in a circle) — shared with
 * A2AConversationList so a pair/conversation's two participants render
 * identically across the switchboard and the classic list. */
export function PairAvatar({ slug }: { slug: string }) {
  return (
    <HelpTip label={getAgentDisplayName(slug)}>
      <div
        className={cn(
          "h-7 w-7 rounded-full border flex items-center justify-center shrink-0",
          TEAM_COLOR_CLASSES[getAgentTeamColor(slug)],
        )}
      >
        <span className="text-[9px] font-bold tracking-tight">
          {getAgentInitials(slug)}
        </span>
      </div>
    </HelpTip>
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
  const isPulsing = usePulseFlash(pulsedAt);

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
          <HelpTip label="Total messages exchanged in this conversation">
            <Badge variant="secondary" className="text-[10px] shrink-0">
              {pair.message_count}
            </Badge>
          </HelpTip>
        )}
      </div>
    </button>
  );
}
