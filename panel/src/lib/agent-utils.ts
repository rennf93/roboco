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
  auditor: "Auditor",
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
  ceo: "CEO",
  CEO: "CEO",
  // Board-adjacent singletons
  "intake-1": "Intake",
  "secretary-1": "Secretary",
  "pr-reviewer-1": "PR Reviewer",
  // Backend-authored notifications/events (not an agent)
  system: "System",
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
export function getAgentDisplayName(
  agentId: string | null | undefined,
): string {
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
  auditor: "AUD",
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
  ceo: "CEO",
  CEO: "CEO",
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

// ---------------------------------------------------------------------------
// Team-color identity (design doc:
// docs/ux_ui/design/02-conversation-first-layout-agent-identity-live-stream.md
// §2). Color is scoped to the CELL an agent belongs to, not a per-agent hue —
// legible at 22-agent scale and needs no new bucket when a cell grows.
// ---------------------------------------------------------------------------

export type AgentTeamColor =
  | "backend"
  | "frontend"
  | "ux_ui"
  | "board"
  | "ceo"
  | "system";

/** Every value here is an existing Tailwind color family already used
 * elsewhere in the codebase at the same `/15` bg + `/40` border weight
 * (`a2a-pair-card.tsx`'s pulse treatment) — no new tokens introduced. */
export const TEAM_COLOR_CLASSES: Record<AgentTeamColor, string> = {
  backend: "bg-blue-500/15 border-blue-500/40 text-blue-700 dark:text-blue-400",
  frontend:
    "bg-violet-500/15 border-violet-500/40 text-violet-700 dark:text-violet-400",
  ux_ui:
    "bg-fuchsia-500/15 border-fuchsia-500/40 text-fuchsia-700 dark:text-fuchsia-400",
  board:
    "bg-amber-500/15 border-amber-500/40 text-amber-700 dark:text-amber-400",
  // The one human gets the app's own accent, not a team bucket.
  ceo: "bg-primary/15 border-primary/40 text-primary",
  system:
    "bg-slate-500/15 border-slate-500/40 text-slate-700 dark:text-slate-400",
};

/**
 * Resolve an agent id (slug or UUID) to its cell color bucket, derived from
 * the slug prefix. Unknown/unresolved slugs fall back to `system` — a color
 * layer is a scanning aid, never something that should throw on a stray id.
 */
export function getAgentTeamColor(
  agentId: string | null | undefined,
): AgentTeamColor {
  const slug = resolveToSlug(agentId);
  if (slug === "ceo" || slug === "CEO") return "ceo";
  if (slug.startsWith("be-")) return "backend";
  if (slug.startsWith("fe-")) return "frontend";
  if (slug.startsWith("ux-")) return "ux_ui";
  if (
    slug === "main-pm" ||
    slug === "product-owner" ||
    slug === "head-marketing" ||
    slug === "auditor"
  ) {
    return "board";
  }
  return "system";
}
