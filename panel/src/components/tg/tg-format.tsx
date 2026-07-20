"use client";

/**
 * Content grooming for the cockpit — the difference between a premium
 * surface and a log viewer is that nothing raw ever reaches the screen:
 * UUIDs become task names (or a short #id8), markdown noise is stripped
 * from one-line previews, and figures render in compact wallet notation.
 */

import { useMemo } from "react";
import { useTasks } from "@/hooks/use-tasks";

const UUID_RE =
  /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi;

/** Replace every UUID in `text` via `resolve` (a task-name lookup); an
 * unresolved id degrades to a short `#a1b2c3d4` handle, never 36 raw chars. */
export function humanizeIds(
  text: string,
  resolve?: (id: string) => string | undefined,
): string {
  return text.replace(UUID_RE, (id) => {
    const name = resolve?.(id.toLowerCase());
    if (!name) return `#${id.slice(0, 8)}`;
    return name.length > 48 ? `${name.slice(0, 47)}…` : name;
  });
}

/** Flatten a (possibly markdown) message body into one clean preview line. */
export function cleanPreview(
  text: string,
  resolve?: (id: string) => string | undefined,
): string {
  const flat = text
    .replace(/```[\s\S]*?```/g, " [code] ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    // Inline emphasis pairs BEFORE structural prefixes — a leading "**bold"
    // must lose its pair as a pair, or the opener gets eaten as a bullet
    // marker and the closer survives mid-string.
    .replace(/[*_~]{1,3}([^*_~]+)[*_~]{1,3}/g, "$1")
    .replace(/^[>#*\-\s]+/gm, "")
    .replace(/\s+/g, " ")
    .trim();
  return humanizeIds(flat, resolve);
}

/** Compact dollar figure: $74.88 · $1.2k · $18k. */
export function fmtUsd(n: number): string {
  if (!Number.isFinite(n)) return "$0";
  if (Math.abs(n) >= 10_000) return `$${(n / 1000).toFixed(0)}k`;
  if (Math.abs(n) >= 1000) return `$${(n / 1000).toFixed(1)}k`;
  return `$${n.toFixed(2)}`;
}

/** Compact token figure: 850 · 45.2k · 197.6M · 1.2B. */
export function fmtTokens(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return "0";
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}k`;
  return `${Math.round(n)}`;
}

/**
 * Task-name lookup shared cockpit-wide. Rides the Board's own query (same
 * key, same 200-task window) so it costs no extra request; ids outside the
 * window simply stay #id8.
 */
export function useTaskNameIndex(): (id: string) => string | undefined {
  const { data: tasks } = useTasks({ limit: 200 });
  return useMemo(() => {
    const index = new Map<string, string>();
    for (const t of tasks ?? []) index.set(t.id.toLowerCase(), t.title);
    return (id: string) => index.get(id);
  }, [tasks]);
}
