/**
 * Pure helpers for the A2A live view (extracted for direct unit testing).
 */

import type { A2AChatMessage } from "@/lib/api/a2a";
import type { ConnectionState } from "@/lib/websocket/connection";

/** The human CEO's fixed slug — never a valid reply target (the CEO composes
 * as itself, so it can't be its own recipient). */
export const CEO_SLUG = "ceo";

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
 * Valid reply recipients for a conversation: both participants, minus the
 * CEO itself when it's a party. A CEO<->agent conversation has exactly one
 * possible target (the agent) — no picker ambiguity, and critically no way
 * to select "ceo" and have the CEO reply to itself.
 */
export function recipientOptions(agentA: string, agentB: string): string[] {
  return [agentA, agentB].filter((slug) => slug !== CEO_SLUG);
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

/** Label for the pane-header connection badge (design doc §3). */
export function connectionStateLabel(state: ConnectionState): string {
  switch (state) {
    case "connected":
      return "Live";
    case "connecting":
      return "Connecting…";
    case "reconnecting":
      return "Reconnecting…";
    case "disconnected":
      return "Offline";
  }
}

/** Dot color for the pane-header connection badge — `connected` is static
 * (no pulse); `connecting`/`reconnecting` share the amber pulsing family,
 * guarded against `prefers-reduced-motion` (design doc §3). */
export function connectionDotClasses(state: ConnectionState): string {
  switch (state) {
    case "connected":
      return "bg-emerald-500";
    case "connecting":
    case "reconnecting":
      return "bg-amber-500 animate-pulse motion-reduce:animate-none";
    case "disconnected":
      return "bg-muted-foreground/40";
  }
}
