/**
 * Pure helpers for the A2A switchboard (org-chart pair cards) — extracted
 * for direct unit testing, same idiom as a2a-utils.ts.
 */

import type { AdminPairSummary } from "@/lib/api/a2a";

/** How long a card stays visibly "hot" after a matching frame (CSS transition). */
export const PAIR_PULSE_FADE_MS = 45_000;

/** Canonical, order-independent key for a pair — stable regardless of which
 * slug is passed first. */
export function pairKey(agentA: string, agentB: string): string {
  return agentA < agentB ? `${agentA}|${agentB}` : `${agentB}|${agentA}`;
}

/** True when an A2A live-stream frame's from/to agents are this unordered pair. */
export function pairMatchesFrame(
  agentA: string,
  agentB: string,
  frameFrom?: string | null,
  frameTo?: string | null,
): boolean {
  if (!frameFrom || !frameTo) return false;
  return (
    (frameFrom === agentA && frameTo === agentB) ||
    (frameFrom === agentB && frameTo === agentA)
  );
}

interface PulseFrame {
  from_agent?: string | null;
  to_agent?: string | null;
  timestamp?: string | null;
}

/**
 * For each pair, the epoch ms of the most recent matching frame — or absent
 * from the map when no frame has matched. Bounded: O(pairs * frames), both
 * small (the switchboard's static matrix, and the live-stream's capped
 * message buffer).
 */
export function latestPulseTimestamps(
  frames: ReadonlyArray<PulseFrame>,
  pairs: ReadonlyArray<Pick<AdminPairSummary, "agent_a" | "agent_b">>,
): Record<string, number> {
  const out: Record<string, number> = {};
  for (const pair of pairs) {
    const key = pairKey(pair.agent_a, pair.agent_b);
    for (const frame of frames) {
      if (
        !pairMatchesFrame(
          pair.agent_a,
          pair.agent_b,
          frame.from_agent,
          frame.to_agent,
        )
      ) {
        continue;
      }
      const ts = frame.timestamp ? new Date(frame.timestamp).getTime() : NaN;
      if (Number.isNaN(ts)) continue;
      if (out[key] === undefined || ts > out[key]) out[key] = ts;
    }
  }
  return out;
}

/** Stable section ordering — the CEO's own 1:1 reach first, then cells
 * (org-chart top-down), the PM chain, board, and the lateral catch-all. */
export const SECTION_ORDER = [
  "ceo",
  "cell-backend",
  "cell-frontend",
  "cell-ux_ui",
  "pm-chain",
  "board",
  "cross",
] as const;

export const SECTION_LABELS: Record<string, string> = {
  ceo: "CEO Direct",
  "cell-backend": "Backend Cell",
  "cell-frontend": "Frontend Cell",
  "cell-ux_ui": "UX/UI Cell",
  "pm-chain": "PM Chain",
  board: "Board",
  cross: "Cross-Team",
};

export interface PairSection {
  groupKey: string;
  label: string;
  pairs: AdminPairSummary[];
}

/** Pairs with history sort first (most recent activity first); never-talked
 * pairs follow in a stable alphabetical order. */
export function sortPairsForSection(
  pairs: ReadonlyArray<AdminPairSummary>,
): AdminPairSummary[] {
  return [...pairs].sort((a, b) => {
    const aTime = a.last_message_at
      ? new Date(a.last_message_at).getTime()
      : null;
    const bTime = b.last_message_at
      ? new Date(b.last_message_at).getTime()
      : null;
    if (aTime !== null && bTime !== null) return bTime - aTime;
    if (aTime !== null) return -1;
    if (bTime !== null) return 1;
    return `${a.agent_a}${a.agent_b}`.localeCompare(`${b.agent_a}${b.agent_b}`);
  });
}

/** Group pairs into ordered, labeled sections for the switchboard grid. */
export function groupPairsBySection(
  pairs: ReadonlyArray<AdminPairSummary>,
): PairSection[] {
  const byGroup = new Map<string, AdminPairSummary[]>();
  for (const pair of pairs) {
    const list = byGroup.get(pair.group_key) ?? [];
    list.push(pair);
    byGroup.set(pair.group_key, list);
  }

  const known = SECTION_ORDER.filter((key) => byGroup.has(key));
  const unknown = [...byGroup.keys()].filter(
    (key) => !(SECTION_ORDER as readonly string[]).includes(key),
  );

  return [...known, ...unknown].map((groupKey) => ({
    groupKey,
    label: SECTION_LABELS[groupKey] ?? groupKey,
    pairs: sortPairsForSection(byGroup.get(groupKey) ?? []),
  }));
}
