"use client";

import type { ReactNode } from "react";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

interface HelpTipProps {
  /** The explanation shown on hover/focus. Falsy → renders the child bare. */
  label: ReactNode;
  /** Tooltip side relative to the trigger. */
  side?: "top" | "bottom" | "left" | "right";
  /** The element to hover; must forward its ref (Button, Badge, span, etc.). */
  children: ReactNode;
}

/**
 * DRY wrapper for the verbose three-element Tooltip pattern. Use for any
 * element that benefits from a hover/focus explanation — icon-only buttons,
 * status/severity/origin badges, health dots, metric numbers, abbreviations.
 * A falsy `label` short-circuits to the bare child, so callers can gate a tip
 * on whether an explanation is available without conditional markup.
 */
export function HelpTip({ label, side = "top", children }: HelpTipProps) {
  if (!label) return <>{children}</>;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent side={side}>{label}</TooltipContent>
    </Tooltip>
  );
}