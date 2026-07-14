"use client";

import * as React from "react";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

export interface SegmentedOption {
  value: string;
  label: string;
}

interface SegmentedControlProps {
  options: SegmentedOption[];
  value: string;
  onValueChange: (value: string) => void;
  "aria-label"?: string;
  className?: string;
}

/**
 * Generic segmented control — a value + N mutually-exclusive options.
 * Built on the Radix Tabs primitive (value/onValueChange, no panels).
 * Used for the metrics time-window selector (24h/7d/30d/90d) and the
 * chart/table view toggle — one primitive, two roles.
 */
export function SegmentedControl({
  options,
  value,
  onValueChange,
  className,
  ...rest
}: SegmentedControlProps) {
  return (
    <Tabs
      value={value}
      onValueChange={onValueChange}
      className={className}
    >
      <TabsList {...rest}>
        {options.map((opt) => (
          <TabsTrigger key={opt.value} value={opt.value}>
            {opt.label}
          </TabsTrigger>
        ))}
      </TabsList>
    </Tabs>
  );
}