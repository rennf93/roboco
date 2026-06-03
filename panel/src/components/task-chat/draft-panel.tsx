"use client";

import { useEffect, useRef, useState } from "react";
import { FileText, ListChecks, Users, Flag, Sparkles } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { ScrollArea } from "@/components/ui/scroll-area";
import { DraftStatusBadge, DraftState } from "./draft-status-badge";
import { DraftUpdate } from "./chat-panel";
import { cn } from "@/lib/utils";

interface DraftPanelProps {
  draft: DraftUpdate;
  draftState: DraftState;
  onSubmitDraft: () => void;
}

/**
 * A single labeled section within the draft.
 * Applies a momentary highlight animation when its content updates.
 *
 * ANIMATION APPROACH (rationale):
 * When a new AI response arrives and updates a draft field, that field
 * receives a ring-highlight for ~1.5 seconds via a CSS class toggle.
 * This creates a visible "thread" connecting the chat message to the
 * specific draft field it updated, making the relationship clear.
 *
 * We chose a ring/outline highlight (Tailwind `ring-2 ring-primary/40`)
 * rather than a background flash for two reasons:
 *   1. It is subtle — it does not shift layout or change the background
 *      color, avoiding visual noise while still being noticeable.
 *   2. It works in both light and dark modes using the existing `ring`
 *      CSS variable from globals.css, so no new colors are needed.
 *
 * The animation is driven by React state + useEffect + setTimeout, so
 * it cleanly re-triggers on every update even if the same field changes
 * multiple times in quick succession.
 */
function DraftField({
  label,
  icon,
  children,
  updated,
}: {
  label: string;
  icon: React.ReactNode;
  children: React.ReactNode;
  updated: boolean;
}) {
  return (
    <div
      className={cn(
        "rounded-lg p-3 transition-all duration-300",
        // Highlight ring fires when `updated` is true — see DraftPanel useEffect below
        updated
          ? "ring-2 ring-primary/40 bg-primary/5"
          : "ring-0 bg-transparent"
      )}
    >
      <div className="flex items-center gap-1.5 mb-1.5">
        <span className="text-muted-foreground" aria-hidden="true">
          {icon}
        </span>
        <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
          {label}
        </span>
      </div>
      {children}
    </div>
  );
}

/**
 * Track which fields have been "recently updated" so we can flash them.
 * Returns a Set of field names that are currently highlighted.
 */
function useFieldHighlight(draft: DraftUpdate): Set<keyof DraftUpdate> {
  const [highlighted, setHighlighted] = useState<Set<keyof DraftUpdate>>(new Set());
  const prevDraftRef = useRef<DraftUpdate>({});
  const timersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  useEffect(() => {
    const prev = prevDraftRef.current;
    const newlyUpdated = new Set<keyof DraftUpdate>();

    // Compare each field: if it changed, mark it for highlight
    (Object.keys(draft) as (keyof DraftUpdate)[]).forEach((key) => {
      const curr = JSON.stringify(draft[key]);
      const old = JSON.stringify(prev[key]);
      if (curr !== old && draft[key] !== undefined) {
        newlyUpdated.add(key);
      }
    });

    if (newlyUpdated.size > 0) {
      setHighlighted((prev) => new Set([...prev, ...newlyUpdated]));

      // Remove highlight for each field after 1.5 seconds
      newlyUpdated.forEach((key) => {
        // Clear any existing timer for this key
        if (timersRef.current.has(key)) {
          clearTimeout(timersRef.current.get(key)!);
        }
        const timer = setTimeout(() => {
          setHighlighted((prev) => {
            const next = new Set(prev);
            next.delete(key);
            return next;
          });
          timersRef.current.delete(key);
        }, 1500);
        timersRef.current.set(key, timer);
      });
    }

    prevDraftRef.current = { ...draft };
  }, [draft]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      timersRef.current.forEach((timer) => clearTimeout(timer));
    };
  }, []);

  return highlighted;
}

export function DraftPanel({ draft, draftState, onSubmitDraft }: DraftPanelProps) {
  const highlighted = useFieldHighlight(draft);
  const isEmpty =
    !draft.title &&
    !draft.description &&
    !draft.acceptanceCriteria?.length &&
    !draft.team &&
    !draft.priority;

  return (
    /*
     * VISUAL HIERARCHY — the draft panel is visually distinct from the chat panel:
     *   - bg-muted/40 background separates it from the white chat area
     *   - left border accent (border-l-4 border-primary/40) marks it as the
     *     "output" side of the conversation
     *   - rounded-xl border wraps the whole panel for enclosure
     *
     * These treatments use only existing Tailwind tokens (bg-muted, border-primary)
     * and require no new colors or design tokens.
     */
    <Card className="h-full flex flex-col gap-0 py-0 overflow-hidden bg-muted/40 border-l-4 border-l-primary/30">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b bg-card shrink-0">
        <div className="flex items-center gap-2">
          <FileText className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
          <span className="text-sm font-medium">Task Draft</span>
        </div>
        {/*
         * DRAFT STATE BADGE — two visually distinct states:
         *   - "still-refining": amber border + spinner icon (motion cue) + "Refining…" text
         *   - "draft-ready-for-review": green border + check icon + "Ready for Review" text
         * Both are distinguishable by icon shape alone, not just color.
         * See draft-status-badge.tsx for full implementation.
         */}
        <DraftStatusBadge state={draftState} />
      </div>

      {/* Draft content */}
      <ScrollArea className="flex-1">
        <div className="p-4 space-y-1">
          {isEmpty ? (
            <div className="flex flex-col items-center justify-center py-12 text-center text-muted-foreground">
              <Sparkles className="h-10 w-10 mb-3 opacity-20" aria-hidden="true" />
              <p className="text-sm font-medium">Draft will appear here</p>
              <p className="text-xs mt-1 max-w-[200px]">
                As you chat, the AI will extract a structured task draft and populate these fields.
              </p>
            </div>
          ) : (
            <>
              {/* Title */}
              {draft.title !== undefined && (
                <DraftField
                  label="Title"
                  icon={<FileText className="h-3.5 w-3.5" />}
                  updated={highlighted.has("title")}
                >
                  <p className="text-sm font-medium">{draft.title}</p>
                </DraftField>
              )}

              {/* Description */}
              {draft.description !== undefined && (
                <>
                  <Separator className="my-1" />
                  <DraftField
                    label="Description"
                    icon={<FileText className="h-3.5 w-3.5" />}
                    updated={highlighted.has("description")}
                  >
                    <p className="text-sm text-muted-foreground leading-relaxed whitespace-pre-wrap">
                      {draft.description}
                    </p>
                  </DraftField>
                </>
              )}

              {/* Acceptance Criteria */}
              {draft.acceptanceCriteria && draft.acceptanceCriteria.length > 0 && (
                <>
                  <Separator className="my-1" />
                  <DraftField
                    label="Acceptance Criteria"
                    icon={<ListChecks className="h-3.5 w-3.5" />}
                    updated={highlighted.has("acceptanceCriteria")}
                  >
                    <ul className="space-y-1.5" role="list">
                      {draft.acceptanceCriteria.map((criterion, index) => (
                        <li key={index} className="flex items-start gap-2 text-sm">
                          <span
                            className="flex-shrink-0 h-4 w-4 rounded-full bg-primary/10 text-primary text-[10px] font-medium flex items-center justify-center mt-0.5"
                            aria-hidden="true"
                          >
                            {index + 1}
                          </span>
                          <span className="text-muted-foreground">{criterion}</span>
                        </li>
                      ))}
                    </ul>
                  </DraftField>
                </>
              )}

              {/* Team and Priority in a row */}
              {(draft.team || draft.priority) && (
                <>
                  <Separator className="my-1" />
                  <div className="grid grid-cols-2 gap-2">
                    {draft.team && (
                      <DraftField
                        label="Team"
                        icon={<Users className="h-3.5 w-3.5" />}
                        updated={highlighted.has("team")}
                      >
                        <Badge variant="secondary" className="text-xs">
                          {draft.team}
                        </Badge>
                      </DraftField>
                    )}
                    {draft.priority && (
                      <DraftField
                        label="Priority"
                        icon={<Flag className="h-3.5 w-3.5" />}
                        updated={highlighted.has("priority")}
                      >
                        <Badge variant="outline" className="text-xs">
                          {draft.priority}
                        </Badge>
                      </DraftField>
                    )}
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </ScrollArea>

      {/* Footer CTA — only show when draft is ready */}
      {draftState === "draft-ready-for-review" && !isEmpty && (
        <div className="border-t px-4 py-3 bg-card shrink-0">
          <Button
            className="w-full gap-2"
            onClick={onSubmitDraft}
            aria-label="Submit task draft for review"
          >
            <Sparkles className="h-4 w-4" aria-hidden="true" />
            Submit for Review
          </Button>
        </div>
      )}
    </Card>
  );
}
