/**
 * Canned cockpit fixtures for `/tg?demo=1` (see demo.ts). Typed against
 * the real API modules so drift breaks typecheck, flavored like a live
 * RoboCo day so the UI reads true.
 */

import type { ReleaseProposal } from "@/lib/api/release";
import type { XPost } from "@/lib/api/x";
import type { VideoPost } from "@/lib/api/video";
import type { RoadmapCycle } from "@/lib/api/roadmap";
import type {
  A2AChatMessage,
  AdminConversationSummary,
  CeoConversationSummary,
} from "@/lib/api/a2a";
import type { TodayBrief } from "@/components/tg/tg-today-tab";
import {
  Complexity,
  NotificationPriority,
  NotificationType,
  TaskNature,
  TaskStatus,
  TaskType,
  Team,
  type Notification,
  type Task,
} from "@/types";

export const DEMO_RELEASE: ReleaseProposal = {
  task_id: "demo-release",
  title: "Release proposal 0.26.0",
  status: "awaiting_ceo_approval",
  report: {
    proposed_version: "0.26.0",
    bump_kind: "minor",
    change_summary: [
      "feat(tg): Mini App V4 — Today brief + native approvals",
      "fix(git): hard-sync target branch to origin",
    ],
    drafted_changelog:
      "## 0.26.0\n\n### Added\n- Telegram Mini App V4: Today brief, native approvals card stack.\n\n### Fixed\n- CEO approve-and-merge no longer fails on a diverged workspace clone.",
    version_bump_plan: [
      "pyproject.toml",
      "roboco/__init__.py",
      "panel/package.json",
    ],
    gaps: [
      {
        category: "docs",
        detail: "docs.roboco.tech Mini App page not yet updated",
      },
    ],
    migration_notes: [],
    gate_state: "green",
  },
};

export const DEMO_X_POSTS: XPost[] = [
  {
    task_id: "demo-x-1",
    source: "x_feature",
    title: "Feature spotlight: findings ledger",
    status: "pending",
    body: "Every QA bounce in RoboCo is now a structured finding — file, line, severity, expected vs actual — not prose. Reviewers stamp them, devs resolve them by id, and nothing gets lost between rounds. The ledger closed our noisiest feedback loop.",
    char_count: 243,
    project_name: "RoboCo",
  },
];

export const DEMO_VIDEO_POSTS: VideoPost[] = [
  {
    task_id: "demo-video-1",
    source: "video_post",
    title: "v0.25.0 release motion",
    status: "pending",
    occasion: "release",
    script: "Three features, three scenes, twelve seconds.",
    platforms: ["x", "tiktok"],
    x_caption: "RoboCo 0.25.0 — the org now cuts its own releases. 🤖",
    tiktok_caption:
      "An AI company that ships itself: RoboCo 0.25.0 was proposed, gated, and published by the fleet — the human just tapped approve.",
    mp4_paths: {},
    render_status: "rendered",
    project_name: "RoboCo",
  },
];

export const DEMO_ROADMAP: RoadmapCycle[] = [
  {
    task_id: "demo-cycle-1",
    title: "Roadmap cycle — operational polish",
    status: "pending",
    goal: "Close the small frictions the metrics keep surfacing",
    items: [
      {
        id: "item-digest",
        title: "Weekly cost digest email",
        description:
          "One Monday-morning email: last week's spend by team, top burners, and the trend line.",
        acceptance_criteria: [
          "Digest renders from existing usage rollups",
          "Opt-out via panel settings",
        ],
        project_slug: "roboco",
        team: "backend",
        priority: 2,
        rationale: "The usage dashboard exists but nobody opens it on Mondays.",
        status: "proposed",
      },
      {
        id: "item-export",
        title: "Task history CSV export",
        description:
          "Export a project's completed-task history with cycle times for offline analysis.",
        acceptance_criteria: ["One-click export from the project page"],
        project_slug: "roboco",
        team: "frontend",
        priority: 3,
        rationale: "Asked for twice during board reviews.",
        status: "proposed",
      },
    ],
  },
];

export const DEMO_TODAY: TodayBrief = {
  needs_you: {
    total: 6,
    awaiting_ceo_count: 1,
    awaiting_ceo: [
      {
        id: "demo-t1",
        title: "Webhook rate limiting — root PR",
        status: "awaiting_ceo_approval",
        team: "backend",
        updated_at: new Date(Date.now() - 40 * 60_000).toISOString(),
      },
    ],
    blocked_count: 1,
    blocked: [
      {
        id: "demo-t2",
        title: "Docs search index rebuild",
        status: "blocked",
        team: "frontend",
        updated_at: new Date(Date.now() - 3 * 3600_000).toISOString(),
      },
    ],
    held_drafts: {
      release_proposals: 1,
      x_posts: 1,
      video_posts: 1,
      roadmap_items: 2,
    },
  },
  fleet: {
    total: 26,
    by_status: { active: 4, idle: 22 },
    working: [
      {
        name: "be-dev-1",
        role: "developer",
        team: "backend",
        task_title: "Webhook rate limiting",
      },
      {
        name: "fe-qa",
        role: "qa",
        team: "frontend",
        task_title: "Metrics time-series review",
      },
      {
        name: "ux-dev-2",
        role: "developer",
        team: "ux_ui",
        task_title: "v0.26.0 release motion",
      },
    ],
  },
  spend: {
    tokens_today: 2_400_000,
    cost_today_usd: 18.72,
    series: [12.4, 9.1, 15.8, 11.2, 21.6, 14.9, 18.72],
    delta_pct: 25.6,
  },
  velocity: { series: [3, 5, 2, 6, 4, 7, 5], week_total: 32 },
  ship: { version: "0.25.0", open_release_proposal: true, ci_fix_tasks: 0 },
};

const _now = Date.now();
const _iso = (minsAgo: number) =>
  new Date(_now - minsAgo * 60_000).toISOString();

function demoTask(
  overrides: Partial<Task> & Pick<Task, "id" | "title" | "status" | "team">,
): Task {
  return {
    description: "",
    constraints: null,
    acceptance_criteria: [],
    priority: 2,
    sequence: 0,
    created_by: "main-pm",
    assigned_to: null,
    parent_task_id: null,
    dependency_ids: [],
    blocker_ids: [],
    created_at: _iso(600),
    updated_at: _iso(30),
    claimed_at: null,
    started_at: null,
    completed_at: null,
    target_date: null,
    estimated_complexity: Complexity.MEDIUM,
    nature: TaskNature.TECHNICAL,
    task_type: TaskType.CODE,
    project_id: "demo-project",
    docs_complete: false,
    pr_created: false,
    pm_approvals: {},
    plan: null,
    checkpoints: [],
    progress_updates: [],
    commits: [],
    dev_notes: null,
    qa_notes: null,
    auditor_notes: null,
    quick_context: null,
    self_verified: false,
    qa_verified: null,
    branch_name: null,
    pr_number: null,
    pr_url: null,
    ...overrides,
  };
}

export const DEMO_TASKS: Task[] = [
  demoTask({
    id: "demo-t1",
    title: "Payments retry queue hardening",
    status: TaskStatus.AWAITING_CEO_APPROVAL,
    team: Team.BACKEND,
    assigned_to: "be-dev-2",
    description:
      "Webhook deliveries that fail mid-flight are retried with exponential backoff and a dead-letter queue after five attempts.",
    acceptance_criteria: [
      "Failed webhook deliveries retry with exponential backoff",
      "A delivery lands in the dead-letter queue after 5 failed attempts",
      "The DLQ is drainable from the admin panel",
    ],
    pr_number: 612,
    pr_url: "https://github.com/example/demo/pull/612",
    branch_name: "feature/backend/DEMO612",
  }),
  demoTask({
    id: "demo-t2",
    title: "Docs search index rebuild",
    status: TaskStatus.BLOCKED,
    team: Team.FRONTEND,
    assigned_to: "fe-dev-1",
    description: "Blocked on the docs-site deploy token rotation.",
    acceptance_criteria: ["Search results reflect pages published this week"],
  }),
  demoTask({
    id: "demo-t3",
    title: "Webhook rate limiting",
    status: TaskStatus.IN_PROGRESS,
    team: Team.BACKEND,
    assigned_to: "be-dev-1",
    acceptance_criteria: [
      "Per-tenant rate limits enforced at the gateway",
      "429 responses carry a Retry-After header",
    ],
  }),
  demoTask({
    id: "demo-t4",
    title: "Metrics time-series review",
    status: TaskStatus.AWAITING_QA,
    team: Team.FRONTEND,
    assigned_to: "fe-qa",
    acceptance_criteria: ["Charts render loading, empty, and error states"],
    pr_number: 604,
    pr_url: "https://github.com/example/demo/pull/604",
  }),
  demoTask({
    id: "demo-t5",
    title: "Onboarding empty-state illustrations",
    status: TaskStatus.NEEDS_REVISION,
    team: Team.UX_UI,
    assigned_to: "ux-dev-1",
    revision_count: 2,
    acceptance_criteria: [
      "Every dashboard empty state has a branded illustration",
    ],
  }),
  demoTask({
    id: "demo-t6",
    title: "v0.26.0 release motion",
    status: TaskStatus.IN_PROGRESS,
    team: Team.UX_UI,
    assigned_to: "ux-dev-2",
    acceptance_criteria: ["Both 9:16 and 1:1 cuts render under 20s"],
  }),
  demoTask({
    id: "demo-t7",
    title: "Self-serve workspace invites",
    status: TaskStatus.PENDING,
    team: Team.BACKEND,
    acceptance_criteria: ["Invited members land in the right workspace role"],
  }),
  demoTask({
    id: "demo-t8",
    title: "Mini App today brief",
    status: TaskStatus.COMPLETED,
    team: Team.FRONTEND,
    assigned_to: "fe-dev-2",
    completed_at: _iso(1400),
    acceptance_criteria: ["One round trip renders the whole brief"],
  }),
];

export const DEMO_NOTIFICATIONS: Notification[] = [
  {
    id: "demo-n1",
    type: NotificationType.BLOCKER_ESCALATION,
    priority: NotificationPriority.URGENT,
    from_agent: "main-pm",
    to_agents: ["ceo-renzo"],
    subject: "Docs search index rebuild is blocked",
    body: "The docs-site deploy token expired; fe-dev-1 cannot push the rebuilt index. Needs a token rotation from the project settings.",
    requires_ack: true,
    is_acknowledged: false,
    is_fully_acknowledged: false,
    is_read: false,
    related_task_id: "demo-t2",
    related_message_ids: [],
    timestamp: _iso(35),
    expires_at: null,
    acked_by: [],
    acked_at: {},
  },
  {
    id: "demo-n2",
    type: NotificationType.APPROVAL,
    priority: NotificationPriority.HIGH,
    from_agent: "main-pm",
    to_agents: ["ceo-renzo"],
    subject: "Payments retry queue hardening awaits your approval",
    body: "QA passed, docs written, PR #612 green. The root PR is assembled and gated — your call.",
    requires_ack: false,
    is_acknowledged: false,
    is_fully_acknowledged: false,
    is_read: false,
    related_task_id: "demo-t1",
    related_message_ids: [],
    timestamp: _iso(95),
    expires_at: null,
    acked_by: [],
    acked_at: {},
  },
  {
    id: "demo-n3",
    type: NotificationType.KNOWLEDGE_SHARE,
    priority: NotificationPriority.NORMAL,
    from_agent: "be-dev-2",
    to_agents: ["ceo-renzo"],
    subject: "Learning: idempotency keys beat retry dedupe",
    body: "Webhook consumers with idempotency keys made the DLQ drain safe to re-run — worth adopting on every external delivery path.",
    requires_ack: false,
    is_acknowledged: true,
    is_fully_acknowledged: true,
    is_read: true,
    related_task_id: null,
    related_message_ids: [],
    timestamp: _iso(400),
    expires_at: null,
    acked_by: ["ceo-renzo"],
    acked_at: {},
  },
];

// ---------------------------------------------------------------------------
// Chat fixtures — the CEO's own DM threads plus watched agent↔agent threads,
// with full transcripts (markdown-flavored like real agent messages).
// ---------------------------------------------------------------------------

export const DEMO_CHAT_MINE: CeoConversationSummary[] = [
  {
    id: "demo-conv-mainpm",
    other_agent: "main-pm",
    topic: null,
    task_id: null,
    status: "active",
    message_count: 4,
    unread_count: 1,
    last_message_at: _iso(12),
    last_message_preview:
      "Wave 2 is queued behind the metrics migration — ETA tomorrow.",
  },
  {
    id: "demo-conv-fedev1",
    other_agent: "fe-dev-1",
    topic: null,
    task_id: null,
    status: "active",
    message_count: 6,
    unread_count: 0,
    last_message_at: _iso(95),
    last_message_preview: "Pushed the fix, PR checks are green.",
  },
  {
    id: "demo-conv-beqa",
    other_agent: "be-qa",
    topic: null,
    task_id: null,
    status: "active",
    message_count: 2,
    unread_count: 0,
    last_message_at: _iso(1440),
    last_message_preview: "Flake was the sandbox port collision, not the test.",
  },
];

export const DEMO_CHAT_FLEET: AdminConversationSummary[] = [
  {
    id: "demo-conv-fepair",
    agent_a: "fe-pm",
    agent_b: "fe-pr-reviewer",
    topic: "PR gate — release docs",
    task_id: "33333333-3333-4333-8333-333333333333",
    status: "active",
    message_count: 9,
    last_message_at: _iso(41),
    last_message_preview: "pr_pass recorded — CI green, per-AC walk attached.",
    created_at: _iso(600),
    updated_at: _iso(41),
  },
  {
    id: "demo-conv-bepair",
    agent_a: "be-dev-1",
    agent_b: "be-qa",
    topic: "QA handoff",
    task_id: "11111111-1111-4111-8111-111111111111",
    status: "active",
    message_count: 5,
    last_message_at: _iso(160),
    last_message_preview: "Re-ran the suite against the sandbox — green.",
    created_at: _iso(900),
    updated_at: _iso(160),
  },
  {
    id: "demo-conv-uxpair",
    agent_a: "ux-dev-2",
    agent_b: "ux-pm",
    topic: null,
    task_id: null,
    status: "resolved",
    message_count: 3,
    last_message_at: _iso(2900),
    last_message_preview: "Frames verified, marking the render check done.",
    created_at: _iso(3100),
    updated_at: _iso(2900),
  },
];

const _msg = (
  id: string,
  conversation_id: string,
  from_agent: string,
  content: string,
  minsAgo: number,
): A2AChatMessage => ({
  id,
  conversation_id,
  from_agent,
  content,
  message_kind: "text",
  response_to_id: null,
  requires_response: false,
  read_at: null,
  created_at: _iso(minsAgo),
  edited_at: null,
});

export const DEMO_CHAT_MESSAGES: Record<string, A2AChatMessage[]> = {
  "demo-conv-mainpm": [
    _msg(
      "dm-1",
      "demo-conv-mainpm",
      "ceo",
      "Where are we on the metrics drilldown wave?",
      70,
    ),
    _msg(
      "dm-2",
      "demo-conv-mainpm",
      "main-pm",
      "Wave 1 merged this morning:\n\n- `panel/src/components/metrics` — time-series + window selector\n- backend rollups untouched\n\nWave 2 (per-agent scorecards) is queued behind the metrics migration — ETA tomorrow.",
      12,
    ),
  ],
  "demo-conv-fedev1": [
    _msg(
      "df-1",
      "demo-conv-fedev1",
      "ceo",
      "The tooltip clipping on the usage chart — yours?",
      130,
    ),
    _msg(
      "df-2",
      "demo-conv-fedev1",
      "fe-dev-1",
      "Yes — `usage-time-series-chart.tsx:84` was mounting the tooltip inside the overflow container. Pushed the fix, PR checks are green.\n\nPR: https://github.com/rennf93/roboco/pull/612",
      95,
    ),
  ],
  "demo-conv-beqa": [
    _msg(
      "db-1",
      "demo-conv-beqa",
      "ceo",
      "That nightly flake on the sandbox suite — real bug?",
      1500,
    ),
    _msg(
      "db-2",
      "demo-conv-beqa",
      "be-qa",
      "Flake was the sandbox port collision, not the test. Two provisioners raced the same host port; the registry retry absorbs it now.",
      1440,
    ),
  ],
  "demo-conv-fepair": [
    _msg(
      "dp-1",
      "demo-conv-fepair",
      "fe-pm",
      "Gate review is yours — assembled PR #609 targets the cell root. Per-AC walk required, docs deliverable included.",
      120,
    ),
    _msg(
      "dp-2",
      "demo-conv-fepair",
      "fe-pr-reviewer",
      "Walked the diff:\n\n1. **AC1** — release notes page `docs/releases/0.26.md:1` ✓\n2. **AC2** — nav entry `docs/mkdocs.yml:48` ✓\n\npr_pass recorded — CI green, per-AC walk attached.",
      41,
    ),
  ],
  "demo-conv-bepair": [
    _msg(
      "dq-1",
      "demo-conv-bepair",
      "be-dev-1",
      "Branch is ready for QA — `feature/backend/A1B2C3D4`. Sandbox creds in the envelope.",
      300,
    ),
    _msg(
      "dq-2",
      "demo-conv-bepair",
      "be-qa",
      "Re-ran the suite against the sandbox — green. Passing to docs.",
      160,
    ),
  ],
  "demo-conv-uxpair": [
    _msg(
      "du-1",
      "demo-conv-uxpair",
      "ux-dev-2",
      "Rendered both cuts, frames extracted to `.previews/` — every brief scene present.",
      3000,
    ),
    _msg(
      "du-2",
      "demo-conv-uxpair",
      "ux-pm",
      "Frames verified, marking the render check done.",
      2900,
    ),
  ],
};
