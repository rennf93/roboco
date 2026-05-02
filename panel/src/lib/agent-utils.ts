/**
 * Agent Display Utilities
 *
 * Utilities for resolving agent IDs (slugs or UUIDs) to human-readable names.
 */

// Static UUID → slug mapping (from backend seeds/initial_data.py)
// NEVER change these after initial deployment
const AGENT_UUIDS: Record<string, string> = {
  // CEO (Human)
  "00000000-0000-0000-0000-000000000001": "ceo",
  // Backend Cell
  "00000000-0000-0000-0001-000000000001": "be-dev-1",
  "00000000-0000-0000-0001-000000000002": "be-dev-2",
  "00000000-0000-0000-0001-000000000003": "be-qa",
  "00000000-0000-0000-0001-000000000004": "be-pm",
  "00000000-0000-0000-0001-000000000005": "be-doc",
  // Frontend Cell
  "00000000-0000-0000-0002-000000000001": "fe-dev-1",
  "00000000-0000-0000-0002-000000000002": "fe-dev-2",
  "00000000-0000-0000-0002-000000000003": "fe-qa",
  "00000000-0000-0000-0002-000000000004": "fe-pm",
  "00000000-0000-0000-0002-000000000005": "fe-doc",
  // UX/UI Cell
  "00000000-0000-0000-0003-000000000001": "ux-dev-1",
  "00000000-0000-0000-0003-000000000002": "ux-dev-2",
  "00000000-0000-0000-0003-000000000003": "ux-qa",
  "00000000-0000-0000-0003-000000000004": "ux-pm",
  "00000000-0000-0000-0003-000000000005": "ux-doc",
  // Board / Management
  "00000000-0000-0000-0004-000000000001": "main-pm",
  "00000000-0000-0000-0004-000000000002": "product-owner",
  "00000000-0000-0000-0004-000000000003": "head-marketing",
  "00000000-0000-0000-0004-000000000004": "auditor",
};

// Static agent name mapping (slug -> display name)
// This matches the backend seed data
const AGENT_NAMES: Record<string, string> = {
  // Board / Management
  "main-pm": "Main PM",
  "product-owner": "Product Owner",
  "head-marketing": "Head Marketing",
  "auditor": "Auditor",
  // Backend Cell
  "be-pm": "Backend PM",
  "be-dev-1": "Backend Dev 1",
  "be-dev-2": "Backend Dev 2",
  "be-qa": "Backend QA",
  "be-doc": "Backend Doc",
  // Frontend Cell
  "fe-pm": "Frontend PM",
  "fe-dev-1": "Frontend Dev 1",
  "fe-dev-2": "Frontend Dev 2",
  "fe-qa": "Frontend QA",
  "fe-doc": "Frontend Doc",
  // UX/UI Cell
  "ux-pm": "UX/UI PM",
  "ux-dev-1": "UX/UI Dev 1",
  "ux-dev-2": "UX/UI Dev 2",
  "ux-qa": "UX/UI QA",
  "ux-doc": "UX/UI Doc",
  // CEO (human)
  "ceo": "CEO",
  "CEO": "CEO",
};

/**
 * Resolve agent ID (UUID or slug) to slug.
 */
export function resolveToSlug(agentId: string | null | undefined): string {
  if (!agentId) return "";
  // If it's a known UUID, return the slug
  if (AGENT_UUIDS[agentId]) {
    return AGENT_UUIDS[agentId];
  }
  // Already a slug or unknown
  return agentId;
}

/**
 * Get display name for an agent ID.
 * Works with both slugs (be-pm) and UUIDs.
 *
 * @param agentId - The agent identifier (slug or UUID)
 * @returns The human-readable name, or the slug, or "Unknown Agent" for unrecognized UUIDs
 */
export function getAgentDisplayName(agentId: string | null | undefined): string {
  if (!agentId) return "Unassigned";

  // First resolve UUID to slug if applicable
  const slug = resolveToSlug(agentId);

  // Check if it's a known slug
  if (AGENT_NAMES[slug]) {
    return AGENT_NAMES[slug];
  }

  // Check if the original was a UUID that we couldn't resolve
  if (agentId.length === 36 && agentId.includes("-")) {
    // Unknown UUID - show first 8 chars
    return agentId.slice(0, 8);
  }

  // It's an unknown slug - return as-is
  return slug;
}

// 3-letter codes for agents (slug -> code)
const AGENT_CODES: Record<string, string> = {
  // Board / Management
  "main-pm": "MPM",
  "product-owner": "PO",
  "head-marketing": "MKT",
  "auditor": "AUD",
  // Backend Cell
  "be-pm": "BPM",
  "be-dev-1": "BD1",
  "be-dev-2": "BD2",
  "be-qa": "BQA",
  "be-doc": "BDC",
  // Frontend Cell
  "fe-pm": "FPM",
  "fe-dev-1": "FD1",
  "fe-dev-2": "FD2",
  "fe-qa": "FQA",
  "fe-doc": "FDC",
  // UX/UI Cell
  "ux-pm": "UPM",
  "ux-dev-1": "UD1",
  "ux-dev-2": "UD2",
  "ux-qa": "UQA",
  "ux-doc": "UDC",
  // CEO
  "ceo": "CEO",
  "CEO": "CEO",
};

/**
 * Get initials for an agent (for avatars).
 *
 * @param agentId - The agent identifier
 * @returns 3-character code
 */
export function getAgentInitials(agentId: string | null | undefined): string {
  if (!agentId) return "???";

  // First resolve UUID to slug if applicable
  const slug = resolveToSlug(agentId);

  // Check for known code
  if (AGENT_CODES[slug]) {
    return AGENT_CODES[slug];
  }

  // Fallback: first 3 chars of display name
  const displayName = getAgentDisplayName(agentId);
  return displayName.slice(0, 3).toUpperCase();
}

/**
 * Check if an agent ID is a known agent slug.
 */
export function isKnownAgent(agentId: string | null | undefined): boolean {
  if (!agentId) return false;
  return agentId in AGENT_NAMES;
}
