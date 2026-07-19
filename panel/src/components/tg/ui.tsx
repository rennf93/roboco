"use client";

/**
 * The cockpit's shared visual language (direction: Telegram palette under
 * RoboCo's typographic voice). Every tab composes these three primitives so
 * density, rhythm, and press-feedback stay identical everywhere:
 * TgSection (a grouped card with a tracked micro-label header), TgRow (a
 * tappable list row with a fixed 44px minimum target), TgStat (a big
 * tabular-nums figure with a caption). Colors always come from the CSS
 * variables — inside Telegram those are the user's own theme (P0's
 * themeParams bridge), so nothing here names a literal color.
 */

import { cn } from "@/lib/utils";
import { ChevronRight } from "lucide-react";
import type { LucideIcon } from "lucide-react";

export function TgSection({
  icon: Icon,
  title,
  trailing,
  children,
  className,
}: {
  icon?: LucideIcon;
  title: string;
  trailing?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section
      className={cn(
        "rounded-xl border bg-card text-card-foreground",
        className,
      )}
    >
      <header className="flex items-center justify-between gap-2 px-3 pb-1 pt-2.5">
        <h2 className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
          {Icon && <Icon className="h-3.5 w-3.5" />}
          {title}
        </h2>
        {trailing}
      </header>
      <div className="px-3 pb-2.5">{children}</div>
    </section>
  );
}

export function TgRow({
  leading,
  title,
  meta,
  lines = 1,
  onPress,
  trailing,
}: {
  leading?: React.ReactNode;
  title: React.ReactNode;
  meta?: React.ReactNode;
  /** Title clamp — 1 for tight lists, 2 when the title IS the content
   * (e.g. an X draft body). */
  lines?: 1 | 2;
  onPress: () => void;
  trailing?: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onPress}
      className="flex min-h-11 w-full items-center gap-3 rounded-lg px-1.5 py-2 text-left transition-colors active:bg-muted"
    >
      {leading}
      <div className="min-w-0 flex-1">
        <p
          className={cn(
            "text-sm font-medium leading-snug",
            lines === 1 ? "truncate" : "line-clamp-2",
          )}
        >
          {title}
        </p>
        {meta && (
          <p className="mt-0.5 truncate text-[11px] leading-tight text-muted-foreground">
            {meta}
          </p>
        )}
      </div>
      {trailing ?? (
        <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground/50" />
      )}
    </button>
  );
}

/** Leading icon tile for rows — the grouped-list glyph square. */
export function TgRowIcon({ icon: Icon }: { icon: LucideIcon }) {
  return (
    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
      <Icon className="h-4.5 w-4.5" />
    </span>
  );
}

export function TgStat({
  value,
  caption,
  tone = "default",
}: {
  value: React.ReactNode;
  caption: React.ReactNode;
  tone?: "default" | "attention";
}) {
  return (
    <div>
      <p
        className={cn(
          "text-[22px] font-semibold leading-tight tracking-tight tabular-nums",
          tone === "attention" && "text-primary",
        )}
      >
        {value}
      </p>
      <p className="mt-0.5 text-[11px] leading-tight text-muted-foreground">
        {caption}
      </p>
    </div>
  );
}
