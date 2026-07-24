// localStorage-backed "recently opened" list for the command palette.
// Read/written only from the browser; every call is a no-op on the server.

const RECENTS_KEY = "roboco-cmd-recents";
const RECENTS_CAP = 10;

export type CommandRecentType = "task" | "agent" | "project" | "page";

export interface CommandRecent {
  type: CommandRecentType;
  id: string;
  title: string;
}

function isCommandRecent(value: unknown): value is CommandRecent {
  if (!value || typeof value !== "object") return false;
  const r = value as Record<string, unknown>;
  return (
    typeof r.id === "string" &&
    typeof r.title === "string" &&
    (r.type === "task" ||
      r.type === "agent" ||
      r.type === "project" ||
      r.type === "page")
  );
}

/** Most-recent-first list of past command-palette selections, capped at 10. */
export function loadRecents(): CommandRecent[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(RECENTS_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isCommandRecent).slice(0, RECENTS_CAP);
  } catch {
    return [];
  }
}

/** Moves `entry` to the front of the recents list (de-duped by type+id). */
export function addRecent(entry: CommandRecent): CommandRecent[] {
  const deduped = loadRecents().filter(
    (r) => !(r.type === entry.type && r.id === entry.id),
  );
  const next = [entry, ...deduped].slice(0, RECENTS_CAP);
  if (typeof window !== "undefined") {
    window.localStorage.setItem(RECENTS_KEY, JSON.stringify(next));
  }
  return next;
}
