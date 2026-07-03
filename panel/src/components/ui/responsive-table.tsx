"use client";

import * as React from "react";
import { useIsMobile } from "@/hooks/use-is-mobile";
import { cn } from "@/lib/utils";

interface ResponsiveTableProps {
  /** The existing desktop `<Table>`, rendered unchanged at `md` and up. */
  table: React.ReactNode;
  /** The stacked card list, rendered below `md` instead of the table. */
  cards: React.ReactNode;
}

/**
 * Single shared switch point for every table call site: below `md`, a data
 * table becomes a stacked card list instead (see ResponsiveTableCard). Only
 * one of the two subtrees mounts at a time — never the desktop table AND its
 * card equivalent together — so an interactive row (dropdowns, buttons) is
 * never duplicated in the DOM.
 *
 * `useIsMobile` defaults to `false` on the server and on the first client
 * render, so this always resolves to `table` until after mount — SSR and the
 * hydration pass render identical markup, then the card branch takes over a
 * tick later on an actual mobile viewport.
 */
export function ResponsiveTable({ table, cards }: ResponsiveTableProps) {
  const isMobile = useIsMobile();
  return <>{isMobile ? cards : table}</>;
}

function ResponsiveTableCardList({
  className,
  ...props
}: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="responsive-table-cards"
      className={cn("space-y-3", className)}
      {...props}
    />
  );
}

function ResponsiveTableCard({
  className,
  ...props
}: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="responsive-table-card"
      className={cn("rounded-lg border bg-card p-4", className)}
      {...props}
    />
  );
}

/** One labeled key/value row inside a card — the mobile analog of a table cell. */
function ResponsiveTableCardRow({
  label,
  children,
  className,
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex items-center justify-between gap-3 py-1 text-sm",
        className,
      )}
    >
      <span className="shrink-0 text-muted-foreground">{label}</span>
      <span className="min-w-0 text-right">{children}</span>
    </div>
  );
}

function ResponsiveTableCardEmpty({
  className,
  ...props
}: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="responsive-table-cards-empty"
      className={cn(
        "rounded-lg border border-dashed p-8 text-center text-muted-foreground",
        className,
      )}
      {...props}
    />
  );
}

export {
  ResponsiveTableCardList,
  ResponsiveTableCard,
  ResponsiveTableCardRow,
  ResponsiveTableCardEmpty,
};
