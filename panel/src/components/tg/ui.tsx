"use client";

/**
 * The cockpit's shared visual language — V6 "soft cockpit": borderless
 * elevated surfaces on a deep slate ground, native type with tabular
 * numerals for figures, one amber accent, generous radii. Every tab
 * composes these primitives so density, rhythm, and press-feedback stay
 * identical everywhere. Colors always come from the CSS variables — inside
 * Telegram those are the user's own theme (the themeParams bridge), so
 * nothing here names a literal surface color.
 */

import { cn } from "@/lib/utils";
import { ArrowLeft, ChevronRight } from "lucide-react";

/** Any icon component — lucide or the cockpit's own duotone glyphs. */
export type TgAnyIcon = React.ComponentType<{ className?: string }>;
import {
  getAgentInitials,
  getAgentTeamColor,
  isKnownAgent,
  TEAM_COLOR_CLASSES,
} from "@/lib/agent-utils";
import { haptics } from "@/lib/telegram/webapp";
import { useBackButton, useTgWebApp } from "@/lib/telegram/hooks";

/** The press language every tappable surface shares: a soft spring-ish
 * scale-down, transform-only. */
export const TG_PRESS =
  "transition-[transform,background-color] duration-200 ease-[cubic-bezier(0.32,0.72,0,1)] active:scale-[0.97]";

/** The elevation language for cards: no outline, just surface contrast
 * plus a hairline top highlight that reads as machined depth. */
export const TG_CARD =
  "rounded-[20px] bg-card shadow-[inset_0_1px_0_rgba(255,255,255,0.04),0_10px_28px_-18px_rgba(0,0,0,0.8)]";

/**
 * A quick-action tile — the cockpit's primary verbs, styled like a native
 * wallet's Transfer/Deposit row. An optional badge count sits on the tile;
 * `accent` fills it with the RoboCo amber for the one action that most
 * wants attention.
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
  /** Disables the button and pulses the icon while an operation runs. */
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
          "relative flex h-[52px] w-full items-center justify-center rounded-2xl",
          TG_PRESS,
          accent
            ? "bg-gradient-to-b from-primary to-primary/85 text-primary-foreground shadow-[0_10px_24px_-10px] shadow-primary/50"
            : "bg-card text-primary shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]",
        )}
      >
        <Icon className={cn("h-5 w-5", busy && "animate-pulse")} />
        {badge !== undefined && badge > 0 && (
          <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-semibold text-white">
            {badge}
          </span>
        )}
      </span>
      <span className="text-[11px] font-medium text-muted-foreground">
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

/**
 * Agent avatar tile — a rounded square carrying the agent's 3-letter code
 * on their CELL's color (the same team-color identity the desktop
 * Switchboard uses), so a list of agents scans by team at a glance. Names
 * that aren't known agents fall back to initials on a stable per-name hue.
 */
export function TgAvatar({
  name,
  active,
  size = "md",
}: {
  name: string;
  active?: boolean;
  size?: "sm" | "md";
}) {
  const dims = size === "sm" ? "h-7 w-7 text-[9px]" : "h-9 w-9 text-[10px]";
  let face: string;
  let code: string;
  if (isKnownAgent(name)) {
    // Tint only — the class map's border-* entries are inert without a
    // border width, and the borderless tile is the point.
    face = TEAM_COLOR_CLASSES[getAgentTeamColor(name)];
    code = getAgentInitials(name);
  } else {
    let hash = 0;
    for (let i = 0; i < name.length; i++)
      hash = (hash + name.charCodeAt(i)) | 0;
    face = _AVATAR_HUES[Math.abs(hash) % _AVATAR_HUES.length];
    code =
      name
        .split(/[-_\s]/)
        .filter(Boolean)
        .slice(0, 2)
        .map((p) => p[0]?.toUpperCase())
        .join("") || "?";
  }
  return (
    <span
      className={cn("relative inline-flex items-center justify-center", dims)}
    >
      <span
        className={cn(
          "flex items-center justify-center rounded-xl font-semibold",
          dims,
          face,
        )}
      >
        {code}
      </span>
      {active && (
        <span className="absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full border-2 border-card bg-emerald-400" />
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
  icon?: TgAnyIcon;
  title: string;
  trailing?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={cn(TG_CARD, "text-card-foreground", className)}>
      <header className="flex items-center justify-between gap-2 px-4 pb-1 pt-3">
        <h2 className="flex items-center gap-1.5 text-[13px] font-semibold text-foreground/90">
          {Icon && <Icon className="h-3.5 w-3.5 text-muted-foreground" />}
          {title}
        </h2>
        {trailing}
      </header>
      <div className="px-4 pb-3">{children}</div>
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
      className="flex min-h-12 w-full items-center gap-3 rounded-xl px-1.5 py-2 text-left transition-colors duration-200 active:bg-white/[0.05]"
    >
      {leading}
      <div className="min-w-0 flex-1">
        <p
          className={cn(
            "text-[15px] font-medium leading-snug",
            lines === 1 ? "truncate" : "line-clamp-2",
          )}
        >
          {title}
        </p>
        {meta && (
          <p className="mt-0.5 truncate text-xs leading-tight text-muted-foreground">
            {meta}
          </p>
        )}
      </div>
      {trailing ?? (
        <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground/40" />
      )}
    </button>
  );
}

/** Leading icon tile for rows — the grouped-list glyph square. A `tone`
 * tints it per row kind so a list of mixed items reads as color-coded
 * rather than a monochrome column. */
const _TILE_TONES: Record<string, string> = {
  amber: "bg-gradient-to-br from-amber-400/25 to-amber-500/5 text-amber-300",
  sky: "bg-gradient-to-br from-sky-400/25 to-sky-500/5 text-sky-300",
  violet:
    "bg-gradient-to-br from-violet-400/25 to-violet-500/5 text-violet-300",
  emerald:
    "bg-gradient-to-br from-emerald-400/25 to-emerald-500/5 text-emerald-300",
  rose: "bg-gradient-to-br from-rose-400/25 to-rose-500/5 text-rose-300",
  muted: "bg-muted/70 text-muted-foreground",
};

export function TgRowIcon({
  icon: Icon,
  tone = "muted",
}: {
  icon: TgAnyIcon;
  tone?: keyof typeof _TILE_TONES | string;
}) {
  return (
    <span
      className={cn(
        "flex h-10 w-10 shrink-0 items-center justify-center rounded-xl",
        _TILE_TONES[tone] ?? _TILE_TONES.muted,
      )}
    >
      <Icon className="h-[18px] w-[18px]" />
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
          "tg-display text-[22px] leading-tight",
          tone === "attention" && "text-primary",
        )}
      >
        {value}
      </p>
      <p className="mt-0.5 text-xs leading-tight text-muted-foreground">
        {caption}
      </p>
    </div>
  );
}

/** Signed percent-change chip — emerald up, rose down, muted flat. */
export function TgDeltaChip({ pct }: { pct: number | null | undefined }) {
  if (pct === null || pct === undefined) return null;
  const up = pct > 0;
  const flat = pct === 0;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-0.5 rounded-full px-2 py-0.5 text-xs font-semibold tabular-nums",
        flat
          ? "bg-muted/60 text-muted-foreground"
          : up
            ? "bg-emerald-500/15 text-emerald-300"
            : "bg-rose-500/15 text-rose-300",
      )}
    >
      {!flat && (up ? "↑" : "↓")}
      {Math.abs(pct).toFixed(Math.abs(pct) >= 100 ? 0 : 1)}%
    </span>
  );
}

/**
 * Segmented control — the wallet-style range picker. Equal-width segments
 * with a sliding thumb (transform-only). Options are stable per mount.
 */
export function TgSegmented<T extends string>({
  options,
  value,
  onChange,
}: {
  options: ReadonlyArray<{ value: T; label: string }>;
  value: T;
  onChange: (next: T) => void;
}) {
  const idx = Math.max(
    0,
    options.findIndex((o) => o.value === value),
  );
  return (
    <div className="relative grid auto-cols-fr grid-flow-col rounded-full bg-muted/50 p-1">
      <span
        aria-hidden="true"
        className="absolute inset-y-1 left-1 rounded-full bg-card shadow-[0_2px_8px_-2px_rgba(0,0,0,0.5)] transition-transform duration-300 ease-[cubic-bezier(0.32,0.72,0,1)]"
        style={{
          width: `calc((100% - 0.5rem) / ${options.length})`,
          transform: `translateX(${idx * 100}%)`,
        }}
      />
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={o.value === value}
          onClick={() => {
            haptics.tap();
            onChange(o.value);
          }}
          className={cn(
            "relative z-10 rounded-full py-1.5 text-center text-[13px] font-medium transition-colors duration-200",
            o.value === value ? "text-foreground" : "text-muted-foreground",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/**
 * A pushed sub-page — the wallet-style drilldown surface. Slides in from
 * the right over the tab area; Telegram's native BackButton dismisses it
 * while it's mounted, with a visible back chevron as the off-Telegram
 * fallback. The parent renders it INSTEAD of the tab content.
 */
export function TgSubPage({
  title,
  subtitle,
  onBack,
  trailing,
  children,
}: {
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  onBack: () => void;
  trailing?: React.ReactNode;
  children: React.ReactNode;
}) {
  const webApp = useTgWebApp();
  useBackButton(onBack);
  return (
    <div className="tg-slide-in">
      <header className="mb-3 flex min-h-9 items-center gap-2">
        {!webApp?.BackButton && (
          <button
            type="button"
            aria-label="Back"
            onClick={onBack}
            className={cn(
              "flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-card text-muted-foreground",
              TG_PRESS,
            )}
          >
            <ArrowLeft className="h-4 w-4" />
          </button>
        )}
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-[17px] font-semibold leading-tight">
            {title}
          </h1>
          {subtitle && (
            <p className="truncate text-xs text-muted-foreground">{subtitle}</p>
          )}
        </div>
        {trailing}
      </header>
      {children}
    </div>
  );
}
