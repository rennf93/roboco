/**
 * Pure filter helpers for the A2A page's filter control — shared by both the
 * switchboard (pairs) and the classic list (conversations) so the same
 * filter state narrows both views (per the per-view rules in
 * docs/ux_ui/design/conversations-filter-control.md §1).
 *
 * All four dimensions filter client-side over the already-fetched page
 * (there are no backend query params for them yet — see the design doc's
 * "Future work" note).
 */

import type { AdminConversationSummary, AdminPairSummary } from "@/lib/api/a2a";

/** The two known `AdminConversationSummary.status` values. */
export type A2AConversationStatus = "active" | "archived";

export interface A2AFilters {
  /** Selected agent slugs — a conversation/pair matches if either
   * participant is selected (empty = no agent filter). */
  agents: string[];
  /** Free-text fragment matched against `task_id` (case-insensitive). */
  taskIdFragment: string;
  /** When true, also match conversations with `task_id === null`. */
  noLinkedTask: boolean;
  /** Selected statuses (empty = no status filter). List-view only. */
  statuses: A2AConversationStatus[];
  /** Inclusive lower bound, `YYYY-MM-DD` (native `<input type="date">`
   * value) or `""` for unset. List-view only. */
  dateFrom: string;
  /** Inclusive upper bound, `YYYY-MM-DD` or `""` for unset. List-view
   * only. */
  dateTo: string;
}

export const EMPTY_A2A_FILTERS: A2AFilters = {
  agents: [],
  taskIdFragment: "",
  noLinkedTask: false,
  statuses: [],
  dateFrom: "",
  dateTo: "",
};

/** Total count of active filter values, one per chip — drives the
 * trigger's `Filters · N` badge. */
export function activeA2AFilterCount(filters: A2AFilters): number {
  return (
    filters.agents.length +
    (filters.taskIdFragment.trim() ? 1 : 0) +
    (filters.noLinkedTask ? 1 : 0) +
    filters.statuses.length +
    (filters.dateFrom ? 1 : 0) +
    (filters.dateTo ? 1 : 0)
  );
}

function matchesAgent(
  agentA: string,
  agentB: string,
  agents: ReadonlyArray<string>,
): boolean {
  if (agents.length === 0) return true;
  return agents.includes(agentA) || agents.includes(agentB);
}

function matchesTask(
  taskId: string | null,
  fragment: string,
  noLinkedTask: boolean,
): boolean {
  const frag = fragment.trim().toLowerCase();
  if (!frag && !noLinkedTask) return true;
  const fragmentMatch = frag
    ? !!taskId && taskId.toLowerCase().includes(frag)
    : false;
  const noLinkedMatch = noLinkedTask ? taskId === null : false;
  return fragmentMatch || noLinkedMatch;
}

function matchesStatus(
  status: string,
  statuses: ReadonlyArray<A2AConversationStatus>,
): boolean {
  if (statuses.length === 0) return true;
  return statuses.includes(status as A2AConversationStatus);
}

/** Day-granularity local-timezone `YYYY-MM-DD`, comparable against a native
 * `<input type="date">` value. */
function localDateOnly(iso: string): string {
  const d = new Date(iso);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function matchesDateRange(
  timestamp: string | null,
  dateFrom: string,
  dateTo: string,
): boolean {
  if (!dateFrom && !dateTo) return true;
  if (!timestamp) return false;
  const day = localDateOnly(timestamp);
  if (dateFrom && day < dateFrom) return false;
  if (dateTo && day > dateTo) return false;
  return true;
}

/** Narrow conversations by all four dimensions — the classic List view. */
export function filterConversations(
  conversations: ReadonlyArray<AdminConversationSummary>,
  filters: A2AFilters,
): AdminConversationSummary[] {
  return conversations.filter(
    (conversation) =>
      matchesAgent(
        conversation.agent_a,
        conversation.agent_b,
        filters.agents,
      ) &&
      matchesTask(
        conversation.task_id,
        filters.taskIdFragment,
        filters.noLinkedTask,
      ) &&
      matchesStatus(conversation.status, filters.statuses) &&
      matchesDateRange(
        conversation.last_message_at ?? conversation.created_at,
        filters.dateFrom,
        filters.dateTo,
      ),
  );
}

/** Narrow switchboard pairs — Agent only (design doc §1 "Per-view
 * applicability"): a pair with no conversation has no task/status/date to
 * filter on. */
export function filterPairs(
  pairs: ReadonlyArray<AdminPairSummary>,
  filters: A2AFilters,
): AdminPairSummary[] {
  return pairs.filter((pair) =>
    matchesAgent(pair.agent_a, pair.agent_b, filters.agents),
  );
}

/** Distinct agent slugs present across the currently loaded pairs +
 * conversations, deduplicated and sorted — the Agent checkbox list's
 * option set (design doc §1, dimension 1). */
export function distinctA2AAgents(
  conversations: ReadonlyArray<AdminConversationSummary>,
  pairs: ReadonlyArray<AdminPairSummary>,
): string[] {
  const slugs = new Set<string>();
  for (const pair of pairs) {
    slugs.add(pair.agent_a);
    slugs.add(pair.agent_b);
  }
  for (const conversation of conversations) {
    slugs.add(conversation.agent_a);
    slugs.add(conversation.agent_b);
  }
  return Array.from(slugs).sort();
}
