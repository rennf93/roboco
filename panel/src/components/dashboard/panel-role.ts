import { CEO_ROLE } from "@/lib/constants";

/**
 * The agent role this panel session presents to the API — the X-Agent-Role the
 * shared axios client injects (lib/api/client.ts, defaulting to the human CEO,
 * lib/constants.ts). Kept behind this one function so CEO-only UI gates stay
 * testable: tests mock this module to simulate an agent-role session.
 */
export function currentPanelRole(): string {
  return CEO_ROLE;
}

export function isCeoRole(role: string): boolean {
  return role === CEO_ROLE;
}