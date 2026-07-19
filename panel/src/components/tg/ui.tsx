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

/**
 * A circular icon action — the cockpit's primary verbs (New task, Approve,
 * Chat, Board), styled like a native wallet's Transfer/Deposit row. An
 * optional badge count sits on the ring; `accent` fills the ring with the
 * RoboCo amber for the one action that most wants attention.
 */
export function TgCircleAction({
  icon: Icon,
  label,
  badge,
  accent = false,
  busy = false,
  onPress,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  badge?: number;
  accent?: boolean;
  /** Disables the button and spins the icon while an operation runs. */
  busy?: boolean;
  onPress: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onPress}
      disabled={busy}
      className="flex flex-1 flex-col items-center gap-1.5 disabled:opacity-60"
    >
      <span
        className={cn(
          "relative flex h-12 w-12 items-center justify-center rounded-full transition-all duration-200 ease-out active:scale-90",
          accent
            ? "bg-gradient-to-b from-primary to-primary/80 text-primary-foreground shadow-[0_8px_20px_-8px] shadow-primary/60"
            : "bg-gradient-to-b from-muted to-muted/60 text-foreground ring-1 ring-inset ring-white/5",
        )}
      >
        <Icon className={cn("h-5 w-5", busy && "animate-pulse")} />
        {badge !== undefined && badge > 0 && (
          <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-semibold text-white">
            {badge}
          </span>
        )}
      </span>
      <span className="tg-display text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
        {label}
      </span>
    </button>
  );
}

const _AVATAR_HUES = [
  "bg-sky-500/20 text-sky-300",
  "bg-emerald-500/20 text-emerald-300",
  "bg-violet-500/20 text-violet-300",
  "bg-amber-500/20 text-amber-300",
  "bg-rose-500/20 text-rose-300",
];

/** Initials avatar with a stable per-name hue and an optional live pulse
 * dot — the fleet strip's agent tokens. */
export function TgAvatar({ name, active }: { name: string; active?: boolean }) {
  const initials = name
    .split(/[-_\s]/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase())
    .join("");
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = (hash + name.charCodeAt(i)) | 0;
  const hue = _AVATAR_HUES[Math.abs(hash) % _AVATAR_HUES.length];
  return (
    <span className="relative inline-flex h-9 w-9 items-center justify-center">
      <span
        className={cn(
          "flex h-9 w-9 items-center justify-center rounded-full text-[11px] font-semibold ring-1 ring-inset ring-white/10",
          hue,
        )}
      >
        {initials || "?"}
      </span>
      {active && (
        <span className="absolute bottom-0 right-0 h-2.5 w-2.5 rounded-full border-2 border-card bg-emerald-400" />
      )}
    </span>
  );
}

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
        <h2 className="tg-display flex items-center gap-1.5 text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
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

/** Leading icon tile for rows — the grouped-list glyph square. A `tone`
 * tints it per row kind so a list of mixed items reads as color-coded
 * rather than a monochrome column. */
const _TILE_TONES: Record<string, string> = {
  amber:
    "bg-gradient-to-br from-amber-400/25 to-amber-500/5 text-amber-300 ring-1 ring-inset ring-amber-400/20",
  sky: "bg-gradient-to-br from-sky-400/25 to-sky-500/5 text-sky-300 ring-1 ring-inset ring-sky-400/20",
  violet:
    "bg-gradient-to-br from-violet-400/25 to-violet-500/5 text-violet-300 ring-1 ring-inset ring-violet-400/20",
  emerald:
    "bg-gradient-to-br from-emerald-400/25 to-emerald-500/5 text-emerald-300 ring-1 ring-inset ring-emerald-400/20",
  rose: "bg-gradient-to-br from-rose-400/25 to-rose-500/5 text-rose-300 ring-1 ring-inset ring-rose-400/20",
  muted: "bg-muted text-muted-foreground ring-1 ring-inset ring-white/5",
};

export function TgRowIcon({
  icon: Icon,
  tone = "muted",
}: {
  icon: LucideIcon;
  tone?: keyof typeof _TILE_TONES | string;
}) {
  return (
    <span
      className={cn(
        "flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px]",
        _TILE_TONES[tone] ?? _TILE_TONES.muted,
      )}
    >
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
          "tg-display text-[22px] leading-tight tabular-nums",
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
