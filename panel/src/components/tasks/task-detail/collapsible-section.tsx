"use client";

import { useState, type ReactNode } from "react";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";
import { exceedsReadabilityThreshold } from "@/lib/content-readability";

interface CollapsibleSectionProps {
  /** Card title content (icon + text + badges as needed) */
  title: ReactNode;
  /** Right-aligned header controls (edit/preview toggles, buttons) — always visible */
  actions?: ReactNode;
  /** Controlled open state (e.g. force-open while a section is mid-edit). Omit for uncontrolled. */
  open?: boolean;
  /**
   * Whether the (uncontrolled) section starts expanded. Takes precedence
   * over `content`-derived collapsing. Omit to let `content` decide, or to
   * default open when neither is given (so nothing visible today disappears).
   */
  defaultOpen?: boolean;
  /**
   * Plain-text representation of the section's body, used to derive
   * `defaultOpen` per the content-readability spec (~10 lines / ~640 chars)
   * when `defaultOpen` is not explicitly set. Ignored otherwise.
   */
  content?: string;
  onOpenChange?: (open: boolean) => void;
  className?: string;
  headerClassName?: string;
  children: ReactNode;
}

/**
 * A Card whose body can be independently collapsed/expanded, so a task with
 * many sections (description, notes, plan) doesn't force continuous
 * scrolling. Collapse/expand is fade + slide (opacity/transform only, via
 * tw-animate-css's animate-in/out) — no height/width property is animated,
 * and prefers-reduced-motion is handled globally in globals.css.
 *
 * Auto-collapse logic (content-readability-spec):
 * - If `defaultOpen` is explicitly set, it takes precedence (e.g., force-open while editing)
 * - Otherwise, if `content` is provided, starts collapsed if content exceeds ~10 lines or ~640 chars
 * - If neither is set, defaults to true (visible by default, safe for new sections)
 *
 * This ensures a task with a long acceptance-criteria list or verbose description
 * doesn't open fully expanded, keeping the page navigable.
 */
export function CollapsibleSection({
  title,
  actions,
  open: openProp,
  defaultOpen,
  content,
  onOpenChange,
  className,
  headerClassName,
  children,
}: CollapsibleSectionProps) {
  // Resolve the starting state: explicit defaultOpen > content-derived > default to true
  const resolvedDefaultOpen =
    defaultOpen ??
    (content !== undefined ? !exceedsReadabilityThreshold(content) : true);
  const [internalOpen, setInternalOpen] = useState(resolvedDefaultOpen);
  const open = openProp ?? internalOpen;
  const setOpen = (next: boolean) => {
    onOpenChange?.(next);
    if (openProp === undefined) setInternalOpen(next);
  };

  return (
    <Card className={className}>
      <Collapsible open={open} onOpenChange={setOpen} className="contents">
        <CardHeader className={cn("pb-3", headerClassName)}>
          <div className="flex items-center justify-between gap-2">
            <Tooltip>
              <TooltipTrigger asChild>
                <CollapsibleTrigger asChild>
                  <button
                    type="button"
                    className="flex min-w-0 flex-1 items-center gap-2 text-left"
                    aria-expanded={open}
                  >
                    <ChevronDown
                      aria-hidden="true"
                      className={cn(
                        "h-4 w-4 shrink-0 text-muted-foreground transition-transform duration-200",
                        !open && "-rotate-90",
                      )}
                    />
                    <CardTitle className="flex min-w-0 items-center gap-2 text-lg">
                      {title}
                    </CardTitle>
                  </button>
                </CollapsibleTrigger>
              </TooltipTrigger>
              <TooltipContent>
                {open ? "Collapse section" : "Expand section"}
              </TooltipContent>
            </Tooltip>
            {actions && (
              <div className="flex shrink-0 items-center gap-2">{actions}</div>
            )}
          </div>
        </CardHeader>
        <CollapsibleContent
          className={cn(
            "duration-200 data-[state=closed]:animate-out data-[state=open]:animate-in",
            "data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
            "data-[state=closed]:slide-out-to-top-1 data-[state=open]:slide-in-from-top-1",
          )}
        >
          <CardContent>{children}</CardContent>
        </CollapsibleContent>
      </Collapsible>
    </Card>
  );
}
