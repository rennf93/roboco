"use client";

import { Radio } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { HelpTip } from "@/components/ui/help-tip";
import type { AdminPairSummary } from "@/lib/api/a2a";
import { A2APairCard } from "./a2a-pair-card";
import { groupPairsBySection, pairKey } from "./a2a-switchboard-utils";

interface A2ASwitchboardProps {
  pairs: AdminPairSummary[];
  /** pairKey(agent_a, agent_b) -> epoch ms of the latest matching frame. */
  pulses: Record<string, number>;
  selectedConversationId: string | null;
  isLoading: boolean;
  onOpenPair: (pair: AdminPairSummary) => void;
}

const SKELETON_COUNT = 9;

/**
 * The org-chart switchboard: every allowed agent pair as a card, grouped
 * into sections (each cell, the PM chain, board, cross-team) and sorted so
 * pairs with history come first within their section.
 */
export function A2ASwitchboard({
  pairs,
  pulses,
  selectedConversationId,
  isLoading,
  onOpenPair,
}: A2ASwitchboardProps) {
  if (isLoading) {
    return (
      <div className="p-2 grid grid-cols-1 sm:grid-cols-2 gap-2">
        {Array.from({ length: SKELETON_COUNT }).map((_, i) => (
          <Skeleton key={i} className="h-16 w-full" />
        ))}
      </div>
    );
  }

  if (pairs.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-muted-foreground">
        <div className="text-center p-4">
          <Radio className="h-8 w-8 mx-auto mb-2 opacity-50" />
          <p className="text-sm">No allowed A2A pairs configured</p>
        </div>
      </div>
    );
  }

  const sections = groupPairsBySection(pairs);

  return (
    <div className="h-full overflow-y-auto p-2 space-y-4">
      {sections.map((section) => (
        <div key={section.groupKey}>
          <HelpTip label="Pairs with prior conversation history are listed first">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2 px-1 w-fit">
              {section.label}
              <span className="ml-1.5 text-muted-foreground/60 normal-case">
                ({section.pairs.length})
              </span>
            </h3>
          </HelpTip>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {section.pairs.map((pair) => {
              const key = pairKey(pair.agent_a, pair.agent_b);
              return (
                <A2APairCard
                  key={key}
                  pair={pair}
                  pulsedAt={pulses[key] ?? null}
                  isSelected={
                    !!pair.conversation_id &&
                    pair.conversation_id === selectedConversationId
                  }
                  onOpen={() => onOpenPair(pair)}
                />
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
