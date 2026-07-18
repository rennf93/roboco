"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, Radio } from "lucide-react";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
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
 * into collapsible sections (CEO direct, each cell, the PM chain, board,
 * cross-team) and sorted so pairs with history come first within their
 * section.
 */
export function A2ASwitchboard({
  pairs,
  pulses,
  selectedConversationId,
  isLoading,
  onOpenPair,
}: A2ASwitchboardProps) {
  // Sections start expanded; collapsed state is per-groupKey, session-local.
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

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
      {sections.map((section) => {
        const isOpen = !collapsed[section.groupKey];
        return (
          <Collapsible
            key={section.groupKey}
            open={isOpen}
            onOpenChange={(open) =>
              setCollapsed((prev) => ({ ...prev, [section.groupKey]: !open }))
            }
          >
            <CollapsibleTrigger asChild>
              <button
                type="button"
                className="flex items-center gap-1 mb-2 px-1 w-fit text-xs font-semibold uppercase tracking-wide text-muted-foreground hover:text-foreground transition-colors"
              >
                {/* Tip goes on the inner span, not the CollapsibleTrigger
                    asChild button — wrapping the trigger itself would clobber
                    its open/closed data-state (same trap as Switch/
                    TabsTrigger). */}
                {isOpen ? (
                  <ChevronDown className="h-3.5 w-3.5" />
                ) : (
                  <ChevronRight className="h-3.5 w-3.5" />
                )}
                <HelpTip label="Pairs with prior conversation history are listed first — click to collapse or expand this section">
                  <span>
                    {section.label}
                    <span className="ml-1.5 text-muted-foreground/60 normal-case">
                      ({section.pairs.length})
                    </span>
                  </span>
                </HelpTip>
              </button>
            </CollapsibleTrigger>
            <CollapsibleContent>
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
            </CollapsibleContent>
          </Collapsible>
        );
      })}
    </div>
  );
}
