import {
  Sparkles,
  ListTodo,
  Kanban,
  GitBranch,
  Briefcase,
  Share2,
  Database,
  Radio,
  Bot,
  BookOpen,
  Shield,
  Activity,
  Building2,
  Cpu,
  Settings,
  type LucideIcon,
} from "lucide-react";

export interface QuickAction {
  id: string;
  label: string;
  icon: LucideIcon;
  href: string;
  tip: string;
}

// Static catalog of every quick-action destination the Overview dashboard can
// jump to. Derived from the panel's real route surface — the sidebar's
// canonical nav list (components/layout/sidebar.tsx) plus the tab-
// parameterized deep links each of those pages actually supports — never an
// invented route. The release/X/video/roadmap approval queues are
// deliberately absent: they already render directly on the Overview page
// itself (see command-center.tsx), so a "quick action" pointing at the page
// the user is already on would be dead weight.
export const QUICK_ACTIONS_REGISTRY: QuickAction[] = [
  {
    id: "new-task",
    label: "New Task",
    icon: Sparkles,
    href: "/prompter",
    tip: "Chat with Intake to draft and confirm a new task, including MegaTask batches",
  },
  {
    id: "tasks",
    label: "Tasks",
    icon: ListTodo,
    href: "/tasks",
    tip: "Full task list — filter, search, and open any task's detail",
  },
  {
    id: "kanban",
    label: "Kanban",
    icon: Kanban,
    href: "/kanban",
    tip: "Task board grouped by lifecycle status",
  },
  {
    id: "git-repository",
    label: "Repository",
    icon: GitBranch,
    href: "/git?tab=repository",
    tip: "Branches, commits, and diffs across every project workspace",
  },
  {
    id: "git-sessions",
    label: "Work Sessions",
    icon: GitBranch,
    href: "/git?tab=sessions",
    tip: "Active agent work sessions — branch, commits, and PR per task",
  },
  {
    id: "workstation-products",
    label: "Products",
    icon: Briefcase,
    href: "/workstation?tab=products",
    tip: "Products the fleet ships against",
  },
  {
    id: "workstation-projects",
    label: "Projects",
    icon: Briefcase,
    href: "/workstation?tab=projects",
    tip: "Manage repos, git tokens, and per-project settings",
  },
  {
    id: "social",
    label: "Social",
    icon: Share2,
    href: "/social",
    tip: "X and TikTok post queues, plus the video pipeline",
  },
  {
    id: "knowledge-base",
    label: "Knowledge Base",
    icon: Database,
    href: "/knowledge-base",
    tip: "Search the RAG corpus — playbooks, learnings, and vault notes",
  },
  {
    id: "a2a",
    label: "A2A",
    icon: Radio,
    href: "/a2a",
    tip: "Live agent-to-agent message switchboard and history",
  },
  {
    id: "agents",
    label: "Agents",
    icon: Bot,
    href: "/agents",
    tip: "Every agent's live state, spawn controls, and activity stream",
  },
  {
    id: "journals",
    label: "Journals",
    icon: BookOpen,
    href: "/journals",
    tip: "Per-agent reflections and learnings",
  },
  {
    id: "auditor",
    label: "Auditor",
    icon: Shield,
    href: "/auditor",
    tip: "Silent-observer quality flags and findings review queue",
  },
  {
    id: "metrics-performance",
    label: "Metrics",
    icon: Activity,
    href: "/metrics?tab=performance",
    tip: "Task velocity, status counts, agent load, and team health",
  },
  {
    id: "metrics-delivery",
    label: "Delivery Metrics",
    icon: Activity,
    href: "/metrics?tab=delivery",
    tip: "Cycle time, bottlenecks, and rework rate reconstructed from the audit log",
  },
  {
    id: "metrics-token-usage",
    label: "Token Usage",
    icon: Activity,
    href: "/metrics?tab=token-usage",
    tip: "Token spend, cost projections, cache efficiency, and per-session detail",
  },
  {
    id: "metrics-scorecards",
    label: "Scorecards",
    icon: Activity,
    href: "/metrics?tab=scorecards",
    tip: "Per-agent and per-team delivery scorecards",
  },
  {
    id: "business-goals",
    label: "Goals",
    icon: Building2,
    href: "/business?tab=goals",
    tip: "CEO-owned charter — north star, brand voice, objectives, constraints",
  },
  {
    id: "business-scorecard",
    label: "Business Scorecard",
    icon: Building2,
    href: "/business?tab=scorecard",
    tip: "Live delivery, spend, and speed metrics against the charter",
  },
  {
    id: "business-secretary",
    label: "Secretary",
    icon: Bot,
    href: "/business?tab=secretary",
    tip: "Chat with your chief-of-staff and confirm or reject pending directives",
  },
  {
    id: "business-pitches",
    label: "Pitches",
    icon: Building2,
    href: "/business?tab=pitches",
    tip: "Board-authored product pitches awaiting your decision",
  },
  {
    id: "ai-providers",
    label: "AI Providers",
    icon: Cpu,
    href: "/settings/ai-providers",
    tip: "Model routing and per-role provider assignments",
  },
  {
    id: "settings",
    label: "Settings",
    icon: Settings,
    href: "/settings",
    tip: "Feature flags, credentials, and panel preferences",
  },
];

const REGISTRY_BY_ID: ReadonlyMap<string, QuickAction> = new Map(
  QUICK_ACTIONS_REGISTRY.map((action) => [action.id, action]),
);

// Curated for the CEO's actual day-to-day workflow — replaces the old
// hardcoded QuickActionsBar (New Task dialog / Spawn Agent / Secretary /
// Journals / Auditor), which never surfaced Tasks, Kanban, Git, or Metrics
// at all despite those being the highest-traffic destinations.
// Includes every destination the legacy QuickActionsBar offered (secretary,
// journals, auditor) — absorbing it must not silently demote any of them out
// of a fresh install's default view.
export const DEFAULT_QUICK_ACTION_IDS: string[] = [
  "new-task",
  "tasks",
  "kanban",
  "git-repository",
  "agents",
  "a2a",
  "business-secretary",
  "journals",
  "auditor",
  "metrics-performance",
  "settings",
];

/**
 * Resolves a stored, ordered id list into real actions, dropping any id that
 * no longer exists in the registry (a stale localStorage entry from a since-
 * removed action) instead of crashing. Order-preserving.
 */
export function resolveQuickActions(ids: readonly string[]): QuickAction[] {
  const resolved: QuickAction[] = [];
  for (const id of ids) {
    const action = REGISTRY_BY_ID.get(id);
    if (action) resolved.push(action);
  }
  return resolved;
}

export function isKnownQuickActionId(id: string): boolean {
  return REGISTRY_BY_ID.has(id);
}
