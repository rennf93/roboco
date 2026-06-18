/**
 * Agent Display Utilities
 *
 * Resolve agent IDs (slugs or UUIDs) to human-readable names.
 *
 * The source of truth is the LIVE roster fetched from `/api/agents` and
 * registered via `registerAgentRoster` (see `useAgentRosterSync`). That roster
 * always matches the backend seed, so it can never drift as agents are added.
 * The static maps below are only an offline / first-paint fallback (and the
 * canonical fixture for tests); they are NOT the authority and may lag the
 * backend until the live roster loads.
 */

// ---------------------------------------------------------------------------
// Live roster — populated at runtime from /api/agents. Keyed by BOTH the
// backend UUID and the slug, so resolution works whichever identifier a caller
// holds (task.assigned_to is a UUID; many UI props pass a slug).
// ---------------------------------------------------------------------------
interface AgentRecord {
  slug: string;
  name: string;
}

const liveByKey = new Map<string, AgentRecord>();

/**
 * Register the live agent roster (from `/api/agents`). Idempotent; later calls
 * overwrite earlier entries so a roster change is reflected immediately.
 */
export function registerAgentRoster(
  agents: ReadonlyArray<{
    uuid?: string | null;
    slug?: string | null;
    name?: string | null;
  }>,
): void {
  for (const agent of agents) {
    const slug = agent.slug ?? undefined;
    if (!slug) continue;
    const record: AgentRecord = { slug, name: agent.name ?? slug };
    liveByKey.set(slug, record);
    if (agent.uuid) liveByKey.set(agent.uuid, record);
  }
}

// Static UUID → slug mapping (from backend seeds/initial_data.py).
// Offline fallback only — the live roster is authoritative. NEVER change a
// UUID here after initial deployment; only add new agents.
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
  // Board-adjacent singletons (CEO-facing / read-only)
  "00000000-0000-0000-0004-000000000005": "intake-1",
  "00000000-0000-0000-0004-000000000006": "secretary-1",
  "00000000-0000-0000-0004-000000000007": "pr-reviewer-1",
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
  // Board-adjacent singletons
  "intake-1": "Intake",
  "secretary-1": "Secretary",
  "pr-reviewer-1": "PR Reviewer",
};

/**
 * Resolve agent ID (UUID or slug) to slug.
 */
export function resolveToSlug(agentId: string | null | undefined): string {
  if (!agentId) return "";
  // Live roster first (covers any agent the backend knows about)...
  const live = liveByKey.get(agentId);
  if (live) return live.slug;
  // ...then the static UUID → slug fallback.
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

  // Live roster first — keyed by both UUID and slug, so a direct hit gives the
  // real name regardless of which identifier the caller passed.
  const liveDirect = liveByKey.get(agentId);
  if (liveDirect) return liveDirect.name;

  // Resolve UUID → slug (live-aware), then try the live roster by slug.
  const slug = resolveToSlug(agentId);
  const liveBySlug = liveByKey.get(slug);
  if (liveBySlug) return liveBySlug.name;

  // Static fallback for known slugs.
  if (AGENT_NAMES[slug]) {
    return AGENT_NAMES[slug];
  }

  // Unresolved UUID (roster not loaded yet and not in the static map) —
  // show the first 8 chars rather than the full 36.
  if (agentId.length === 36 && agentId.includes("-")) {
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
  // Board-adjacent singletons
  "intake-1": "INT",
  "secretary-1": "SEC",
  "pr-reviewer-1": "PRR",
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
  return liveByKey.has(agentId) || agentId in AGENT_NAMES;
}
