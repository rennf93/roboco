/**
 * Canned cockpit fixtures for `/tg?demo=1` (see demo.ts). Typed against
 * the real API modules so drift breaks typecheck, flavored like a live
 * RoboCo day so the UI reads true.
 */

import type { ReleaseProposal } from "@/lib/api/release";
import type { XPost } from "@/lib/api/x";
import type { VideoPost } from "@/lib/api/video";
import type { RoadmapCycle } from "@/lib/api/roadmap";
import type { TodayBrief } from "@/components/tg/tg-today-tab";

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
    version_bump_plan: ["pyproject.toml", "roboco/__init__.py", "panel/package.json"],
    gaps: [{ category: "docs", detail: "docs.roboco.tech Mini App page not yet updated" }],
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
    title: "Roadmap cycle — reduce CEO toil",
    status: "pending",
    goal: "Every daily CEO chore reachable from the phone in under 10 seconds",
    items: [
      {
        id: "item-cmd",
        title: "Bot command tier (/agents, /usage, /blocked)",
        description:
          "Deterministic half-second reads over the fleet, spend, and stuck work, straight from the Telegram keyboard.",
        acceptance_criteria: [
          "Commands registered via setMyCommands at startup",
          "Every reply deep-links into the Mini App",
        ],
        project_slug: "roboco",
        team: "backend",
        priority: 1,
        rationale: "The Mini App covers depth; commands cover glances.",
        status: "proposed",
      },
      {
        id: "item-intake",
        title: "/newtask → live Intake interview in-thread",
        description:
          "Dictate an idea from the phone; the Prompter interviews you in the chat thread and the draft rides board review.",
        acceptance_criteria: ["Interview happens as Telegram messages"],
        project_slug: "roboco",
        team: "backend",
        priority: 2,
        rationale: "Idea capture is the CEO's most mobile moment.",
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
        title: "Forge Phase 2 — GitLab/Gitea transports",
        status: "awaiting_ceo_approval",
        team: "backend",
        updated_at: new Date(Date.now() - 40 * 60_000).toISOString(),
      },
    ],
    blocked_count: 1,
    blocked: [
      {
        id: "demo-t2",
        title: "Panel scorecards N+1 sweep",
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
      { name: "be-dev-1", role: "developer", team: "backend", task_title: "GitProvider seam — Gitea transport" },
      { name: "fe-qa", role: "qa", team: "frontend", task_title: "Metrics time-series review" },
      { name: "ux-dev-2", role: "developer", team: "ux_ui", task_title: "v0.26.0 release motion" },
    ],
  },
  spend: { tokens_today: 2_400_000, cost_today_usd: 18.72 },
  ship: { version: "0.25.0", open_release_proposal: true, ci_fix_tasks: 0 },
};
