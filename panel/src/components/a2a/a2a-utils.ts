/**
 * Pure helpers for the A2A live view (extracted for direct unit testing).
 */

import type { A2AChatMessage } from "@/lib/api/a2a";

/**
 * Slug of the sender of the chronologically latest message, or null when the
 * transcript is empty. Sorts defensively — the API contract is oldest-first,
 * but the default-recipient pick must not depend on payload ordering.
 */
export function lastSenderOf(
  messages: ReadonlyArray<Pick<A2AChatMessage, "from_agent" | "created_at">>,
): string | null {
  if (messages.length === 0) return null;
  const sorted = [...messages].sort(
    (a, b) =>
      new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
  );
  return sorted[sorted.length - 1].from_agent;
}

/**
 * Default reply recipient: the participant who spoke last (the natural
 * "answer them" target), falling back to agent_a when the transcript is empty
 * or the last sender is not one of the two participants.
 */
export function pickDefaultRecipient(
  agentA: string,
  agentB: string,
  lastSender: string | null,
): string {
  if (lastSender === agentA || lastSender === agentB) return lastSender;
  return agentA;
}
