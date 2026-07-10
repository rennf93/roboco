/**
 * Pure filter helpers for the A2A page's filter bar — shared by both the
 * switchboard (pairs) and the classic list (conversations) so the same two
 * inputs narrow both views to the matching subset.
 */

import type { AdminConversationSummary, AdminPairSummary } from "@/lib/api/a2a";
import { getAgentDisplayName } from "@/lib/agent-utils";

/** "active" narrows to items with a live/talked conversation; "all" is no
 * status narrowing. */
export type A2AStatusFilter = "active" | "all";

function matchesSearch(
  query: string,
  ...fields: Array<string | null | undefined>
): boolean {
  if (!query) return true;
  const haystack = fields
    .filter((field): field is string => !!field)
    .join(" ")
    .toLowerCase();
  return haystack.includes(query);
}

/** Narrow conversations to the active/all status (conversation.status)
 * and free-text search over both agents' slugs/display names and the
 * topic. */
export function filterConversations(
  conversations: ReadonlyArray<AdminConversationSummary>,
  status: A2AStatusFilter,
  search: string,
): AdminConversationSummary[] {
  const query = search.trim().toLowerCase();
  return conversations.filter((conversation) => {
    if (status === "active" && conversation.status !== "active") return false;
    return matchesSearch(
      query,
      conversation.agent_a,
      conversation.agent_b,
      getAgentDisplayName(conversation.agent_a),
      getAgentDisplayName(conversation.agent_b),
      conversation.topic,
    );
  });
}

/** Narrow switchboard pairs to the active/all status (active = the pair has
 * a representative conversation, i.e. has actually A2A'd) and free-text
 * search over both agents' slugs/display names. */
export function filterPairs(
  pairs: ReadonlyArray<AdminPairSummary>,
  status: A2AStatusFilter,
  search: string,
): AdminPairSummary[] {
  const query = search.trim().toLowerCase();
  return pairs.filter((pair) => {
    if (status === "active" && !pair.conversation_id) return false;
    return matchesSearch(
      query,
      pair.agent_a,
      pair.agent_b,
      getAgentDisplayName(pair.agent_a),
      getAgentDisplayName(pair.agent_b),
    );
  });
}
