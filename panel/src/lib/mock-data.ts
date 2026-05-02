import {
  Task,
  TaskStatus,
  Team,
  Complexity,
  AgentRole,
  NotificationType,
  NotificationPriority,
  JournalEntryType,
  ChannelType,
  SessionStatus,
  SessionScope,
  MessageType,
  FlagSeverity,
  TaskNature,
  TaskType,
} from "@/types";

// Simple mock ID generator
let _id = 0;
const mockId = () => `mock-${++_id}`;

// Agent IDs - slugs (not UUIDs) except CEO
export const AGENT_IDS = {
  // CEO (human) - only one with UUID
  ceo: "00000000-0000-0000-0000-000000000001",
  // Board/Management (team=null in seed data)
  productOwner: "product-owner",
  mainPm: "main-pm",
  headMarketing: "head-marketing",
  auditor: "auditor",
  // Backend Cell
  bePm: "be-pm",
  beDev1: "be-dev-1",
  beDev2: "be-dev-2",
  beQa: "be-qa",
  beDoc: "be-doc",
  // Frontend Cell
  fePm: "fe-pm",
  feDev1: "fe-dev-1",
  feDev2: "fe-dev-2",
  feQa: "fe-qa",
  feDoc: "fe-doc",
  // UX/UI Cell
  uxPm: "ux-pm",
  uxDev1: "ux-dev-1",
  uxDev2: "ux-dev-2",
  uxQa: "ux-qa",
  uxDoc: "ux-doc",
};

export const TASK_IDS = {
  task1: "11111111-1111-1111-1111-111111111111",
  task2: "22222222-2222-2222-2222-222222222222",
  task3: "33333333-3333-3333-3333-333333333333",
  task4: "44444444-4444-4444-4444-444444444444",
  task5: "55555555-5555-5555-5555-555555555555",
  task6: "66666666-6666-6666-6666-666666666666",
};

// Mock project IDs (all tasks require a project for git workflow)
export const PROJECT_IDS = {
  roboco: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  robocoPanel: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
};

export const CHANNEL_IDS = {
  backendCell: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  frontendCell: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
  uxuiCell: "cccccccc-cccc-cccc-cccc-cccccccccccc",
  devAll: "dddddddd-dddd-dddd-dddd-dddddddddddd",
  qaAll: "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
  pmAll: "ffffffff-ffff-ffff-ffff-ffffffffffff",
  announcements: "00000000-0000-0000-0000-000000000100",
  allHands: "00000000-0000-0000-0000-000000000101",
};

// Timestamps - computed dynamically to stay relative
const getNow = () => new Date();
const getMinutesAgo = (mins: number) => new Date(Date.now() - mins * 60 * 1000);
const getHoursAgo = (hours: number) => new Date(Date.now() - hours * 60 * 60 * 1000);
const getDaysAgo = (days: number) => new Date(Date.now() - days * 24 * 60 * 60 * 1000);

// Static references for initial data (these get stale but are used for tasks/etc)
const now = getNow();
const hourAgo = getHoursAgo(1);
const dayAgo = getDaysAgo(1);
const weekAgo = getDaysAgo(7);

// =============================================================================
// MOCK AGENTS - Matching backend seed data exactly
// =============================================================================

interface MockAgent {
  id: string;
  name: string;
  slug: string;
  role: AgentRole;
  team: Team | null;
}

export const mockAgents: MockAgent[] = [
  // Backend Cell
  { id: AGENT_IDS.beDev1, name: "Backend Developer 1", slug: "be-dev-1", role: AgentRole.DEVELOPER, team: Team.BACKEND },
  { id: AGENT_IDS.beDev2, name: "Backend Developer 2", slug: "be-dev-2", role: AgentRole.DEVELOPER, team: Team.BACKEND },
  { id: AGENT_IDS.beQa, name: "Backend QA", slug: "be-qa", role: AgentRole.QA, team: Team.BACKEND },
  { id: AGENT_IDS.bePm, name: "Backend PM", slug: "be-pm", role: AgentRole.CELL_PM, team: Team.BACKEND },
  { id: AGENT_IDS.beDoc, name: "Backend Documenter", slug: "be-doc", role: AgentRole.DOCUMENTER, team: Team.BACKEND },
  // Frontend Cell
  { id: AGENT_IDS.feDev1, name: "Frontend Developer 1", slug: "fe-dev-1", role: AgentRole.DEVELOPER, team: Team.FRONTEND },
  { id: AGENT_IDS.feDev2, name: "Frontend Developer 2", slug: "fe-dev-2", role: AgentRole.DEVELOPER, team: Team.FRONTEND },
  { id: AGENT_IDS.feQa, name: "Frontend QA", slug: "fe-qa", role: AgentRole.QA, team: Team.FRONTEND },
  { id: AGENT_IDS.fePm, name: "Frontend PM", slug: "fe-pm", role: AgentRole.CELL_PM, team: Team.FRONTEND },
  { id: AGENT_IDS.feDoc, name: "Frontend Documenter", slug: "fe-doc", role: AgentRole.DOCUMENTER, team: Team.FRONTEND },
  // UX/UI Cell (only 1 dev per seed data)
  { id: AGENT_IDS.uxDev1, name: "UX/UI Developer 1", slug: "ux-dev-1", role: AgentRole.DEVELOPER, team: Team.UX_UI },
  { id: AGENT_IDS.uxDev2, name: "UX/UI Developer 2", slug: "ux-dev-2", role: AgentRole.DEVELOPER, team: Team.UX_UI },
  { id: AGENT_IDS.uxQa, name: "UX/UI QA", slug: "ux-qa", role: AgentRole.QA, team: Team.UX_UI },
  { id: AGENT_IDS.uxPm, name: "UX/UI PM", slug: "ux-pm", role: AgentRole.CELL_PM, team: Team.UX_UI },
  { id: AGENT_IDS.uxDoc, name: "UX/UI Documenter", slug: "ux-doc", role: AgentRole.DOCUMENTER, team: Team.UX_UI },
  // Board/Management (team=null per seed data)
  { id: AGENT_IDS.mainPm, name: "Main PM", slug: "main-pm", role: AgentRole.MAIN_PM, team: null },
  { id: AGENT_IDS.productOwner, name: "Product Owner", slug: "product-owner", role: AgentRole.PRODUCT_OWNER, team: null },
  { id: AGENT_IDS.headMarketing, name: "Head of Marketing", slug: "head-marketing", role: AgentRole.HEAD_MARKETING, team: null },
  { id: AGENT_IDS.auditor, name: "Auditor", slug: "auditor", role: AgentRole.AUDITOR, team: null },
  // CEO (Human - Renzo)
  { id: AGENT_IDS.ceo, name: "Renzo", slug: "ceo", role: AgentRole.CEO, team: null },
];

// =============================================================================
// MOCK TASKS - Full structure matching backend TaskResponse schema
// =============================================================================

export const mockTasks: Task[] = [
  {
    id: TASK_IDS.task1,
    title: "Implement user authentication flow",
    description: `## Overview
Implement a complete user authentication system including:

- Login with email/password
- OAuth integration (Google, GitHub)
- Password reset functionality
- Session management

## Technical Requirements
- Use JWT tokens for session management
- Implement refresh token rotation
- Add rate limiting on auth endpoints`,
    team: Team.BACKEND,
    priority: 1,
    sequence: 1,
    status: TaskStatus.IN_PROGRESS,
    estimated_complexity: Complexity.HIGH,
    nature: TaskNature.TECHNICAL,
    task_type: TaskType.CODE,
    project_id: PROJECT_IDS.roboco,
    docs_complete: false,
    pr_created: false,
    pm_approvals: {},
    acceptance_criteria: [
      "Users can log in with email/password",
      "Users can log in with Google OAuth",
      "Password reset emails are sent correctly",
      "Sessions expire after 24 hours of inactivity",
    ],
    dependency_ids: [],
    blocker_ids: [],
    parent_task_id: null,
    target_date: null,
    created_at: dayAgo.toISOString(),
    updated_at: hourAgo.toISOString(),
    created_by: AGENT_IDS.productOwner,
    assigned_to: AGENT_IDS.beDev1,
    claimed_at: dayAgo.toISOString(),
    started_at: hourAgo.toISOString(),
    completed_at: null,
    self_verified: false,
    qa_verified: null,
    sessions: [],
    branch_name: "feature/TASK-001-user-auth",
    pr_number: null,
    pr_url: null,
    plan: {
      approach: `## Implementation Strategy

1. **Database Schema**: Create users table with proper indexes
2. **Auth Service**: Implement core auth logic
3. **API Endpoints**: Build REST endpoints
4. **OAuth Integration**: Add Google/GitHub providers
5. **Testing**: Write comprehensive tests`,
      sub_tasks: [
        { id: mockId(), title: "Create database migrations", description: null, order: 1, completed: true, estimated_hours: 2, notes: null },
        { id: mockId(), title: "Implement auth service", description: null, order: 2, completed: true, estimated_hours: 4, notes: null },
        { id: mockId(), title: "Build API endpoints", description: null, order: 3, completed: false, estimated_hours: 4, notes: null },
        { id: mockId(), title: "Add OAuth providers", description: null, order: 4, completed: false, estimated_hours: 3, notes: null },
        { id: mockId(), title: "Write tests", description: null, order: 5, completed: false, estimated_hours: 3, notes: null },
      ],
      technical_considerations: [
        "Consider using Redis for session storage",
        "Implement proper error handling",
        "Add request logging for audit trail",
      ],
      risks: [{ description: "OAuth provider API changes", severity: "medium", mitigation: "Use official SDKs and monitor changelogs" }],
      open_questions: [{ question: "Should we support 2FA in this iteration?", answer: "No, defer to Phase 2", answered_by: AGENT_IDS.productOwner, answered_at: dayAgo.toISOString() }],
    },
    progress_updates: [
      { timestamp: hourAgo.toISOString(), message: "Completed database migrations and auth service implementation", agent_id: AGENT_IDS.beDev1, percentage: 40 },
    ],
    checkpoints: [],
    commits: [{ hash: "abc123def456", message: "feat: add user authentication schema", timestamp: dayAgo.toISOString(), author_agent_id: AGENT_IDS.beDev1 }],
    dev_notes: "Using bcrypt with 12 rounds for password hashing.",
    qa_notes: null,
    auditor_notes: null,
    quick_context: "Currently implementing API endpoints. Auth service is ready.",
  },
  {
    id: TASK_IDS.task2,
    title: "Build dashboard components",
    description: `Create reusable dashboard components for the CEO command center.

## Components Needed
- Key metrics panel
- Team health cards
- Activity feed
- Quick actions bar`,
    team: Team.FRONTEND,
    priority: 2,
    sequence: 2,
    status: TaskStatus.PENDING,
    estimated_complexity: Complexity.MEDIUM,
    nature: TaskNature.TECHNICAL,
    task_type: TaskType.CODE,
    project_id: PROJECT_IDS.roboco,
    docs_complete: false,
    pr_created: false,
    pm_approvals: {},
    acceptance_criteria: ["All components are responsive", "Components follow design system", "Unit tests cover 80%+ of code"],
    dependency_ids: [],
    blocker_ids: [],
    parent_task_id: TASK_IDS.task1, // Child of "Implement user authentication flow"
    target_date: null,
    created_at: dayAgo.toISOString(),
    updated_at: dayAgo.toISOString(),
    created_by: AGENT_IDS.fePm,
    assigned_to: null,
    claimed_at: null,
    started_at: null,
    completed_at: null,
    self_verified: false,
    qa_verified: null,
    sessions: [],
    branch_name: null,
    pr_number: null,
    pr_url: null,
    plan: null,
    progress_updates: [],
    checkpoints: [],
    commits: [],
    dev_notes: null,
    qa_notes: null,
    auditor_notes: null,
    quick_context: null,
  },
  {
    id: TASK_IDS.task3,
    title: "Fix pagination bug in task list",
    description: `The task list pagination breaks when filtering by status.

## Steps to Reproduce
1. Go to /tasks
2. Filter by "In Progress"
3. Click page 2
4. Filter resets unexpectedly

## Expected Behavior
Pagination should maintain filter state.`,
    team: Team.FRONTEND,
    priority: 0,
    sequence: 3,
    status: TaskStatus.CLAIMED,
    estimated_complexity: Complexity.LOW,
    nature: TaskNature.TECHNICAL,
    task_type: TaskType.CODE,
    project_id: PROJECT_IDS.roboco,
    docs_complete: false,
    pr_created: false,
    pm_approvals: {},
    acceptance_criteria: ["Pagination maintains filter state", "URL params reflect current filters", "No regression in existing functionality"],
    dependency_ids: [],
    blocker_ids: [],
    parent_task_id: TASK_IDS.task1, // Child of "Implement user authentication flow"
    target_date: null,
    created_at: hourAgo.toISOString(),
    updated_at: hourAgo.toISOString(),
    created_by: AGENT_IDS.fePm,
    assigned_to: AGENT_IDS.feDev1,
    claimed_at: hourAgo.toISOString(),
    started_at: null,
    completed_at: null,
    self_verified: false,
    qa_verified: null,
    sessions: [],
    branch_name: "fix/TASK-003-pagination-bug",
    pr_number: null,
    pr_url: null,
    plan: null,
    progress_updates: [],
    checkpoints: [],
    commits: [],
    dev_notes: null,
    qa_notes: null,
    auditor_notes: null,
    quick_context: null,
  },
  {
    id: TASK_IDS.task4,
    title: "Design new onboarding flow",
    description: `Create wireframes and mockups for the new user onboarding experience.

The flow should guide new users through:
- Account setup
- Team configuration
- First task creation`,
    team: Team.UX_UI,
    priority: 2,
    sequence: 4,
    status: TaskStatus.AWAITING_QA,
    estimated_complexity: Complexity.MEDIUM,
    nature: TaskNature.TECHNICAL,
    task_type: TaskType.DESIGN,
    project_id: PROJECT_IDS.robocoPanel,
    docs_complete: false,
    pr_created: false,
    pm_approvals: {},
    acceptance_criteria: ["Wireframes for all screens", "Interactive prototype", "User flow documentation"],
    dependency_ids: [],
    blocker_ids: [],
    parent_task_id: null,
    target_date: null,
    created_at: dayAgo.toISOString(),
    updated_at: hourAgo.toISOString(),
    created_by: AGENT_IDS.uxPm,
    assigned_to: AGENT_IDS.uxDev1,
    claimed_at: dayAgo.toISOString(),
    started_at: dayAgo.toISOString(),
    completed_at: hourAgo.toISOString(),
    self_verified: true,
    qa_verified: null,
    sessions: [],
    branch_name: "feature/TASK-004-onboarding-design",
    pr_number: 42,
    pr_url: "https://github.com/roboco/roboco/pull/42",
    plan: {
      approach: "Create lo-fi wireframes first, then hi-fi mockups after stakeholder approval.",
      sub_tasks: [
        { id: mockId(), title: "User research", description: null, order: 1, completed: true, estimated_hours: 2, notes: null },
        { id: mockId(), title: "Lo-fi wireframes", description: null, order: 2, completed: true, estimated_hours: 2, notes: null },
        { id: mockId(), title: "Hi-fi mockups", description: null, order: 3, completed: true, estimated_hours: 3, notes: null },
        { id: mockId(), title: "Interactive prototype", description: null, order: 4, completed: true, estimated_hours: 1, notes: null },
      ],
      technical_considerations: [],
      risks: [],
      open_questions: [],
    },
    progress_updates: [
      { timestamp: dayAgo.toISOString(), message: "Completed user research and started wireframes", agent_id: AGENT_IDS.uxDev1, percentage: 30 },
      { timestamp: hourAgo.toISOString(), message: "Finished all mockups and prototype, submitting for QA", agent_id: AGENT_IDS.uxDev1, percentage: 100 },
    ],
    checkpoints: [],
    commits: [],
    dev_notes: "Used Figma for all designs. Prototype link in the team channel.",
    qa_notes: null,
    auditor_notes: null,
    quick_context: "Ready for QA review. All deliverables complete.",
  },
  {
    id: TASK_IDS.task5,
    title: "Optimize database queries",
    description: `Several API endpoints are slow due to unoptimized queries.

## Affected Endpoints
- GET /tasks (> 500ms)
- GET /dashboard/metrics (> 1s)

## Goal
Reduce response time to < 100ms for all endpoints.`,
    team: Team.BACKEND,
    priority: 1,
    sequence: 5,
    status: TaskStatus.BLOCKED,
    estimated_complexity: Complexity.HIGH,
    nature: TaskNature.TECHNICAL,
    task_type: TaskType.CODE,
    project_id: PROJECT_IDS.roboco,
    docs_complete: false,
    pr_created: false,
    pm_approvals: {},
    acceptance_criteria: ["All listed endpoints respond in < 100ms", "Query execution plans are documented", "No N+1 queries remain"],
    dependency_ids: [],
    blocker_ids: [TASK_IDS.task1],
    parent_task_id: null,
    target_date: null,
    created_at: dayAgo.toISOString(),
    updated_at: hourAgo.toISOString(),
    created_by: AGENT_IDS.bePm,
    assigned_to: AGENT_IDS.beDev2,
    claimed_at: dayAgo.toISOString(),
    started_at: dayAgo.toISOString(),
    completed_at: null,
    self_verified: false,
    qa_verified: null,
    sessions: [],
    branch_name: "perf/TASK-005-db-optimization",
    pr_number: null,
    pr_url: null,
    plan: null,
    progress_updates: [{ timestamp: hourAgo.toISOString(), message: "Blocked waiting for auth changes to merge", agent_id: AGENT_IDS.beDev2, percentage: 10 }],
    checkpoints: [],
    commits: [],
    dev_notes: "Need to wait for task #1 to complete before proceeding.",
    qa_notes: null,
    auditor_notes: null,
    quick_context: "Blocked by auth implementation.",
  },
  {
    id: TASK_IDS.task6,
    title: "Write API documentation",
    description: `Document all public API endpoints using OpenAPI/Swagger.

Include:
- Endpoint descriptions
- Request/response schemas
- Authentication requirements
- Example requests`,
    team: Team.BACKEND,
    priority: 3,
    sequence: 6,
    status: TaskStatus.COMPLETED,
    estimated_complexity: Complexity.LOW,
    nature: TaskNature.TECHNICAL,
    task_type: TaskType.DOCUMENTATION,
    project_id: PROJECT_IDS.roboco,
    docs_complete: true,
    pr_created: true,
    pm_approvals: { main_pm: true },
    acceptance_criteria: ["All endpoints documented", "Swagger UI accessible at /docs", "Examples for each endpoint"],
    dependency_ids: [],
    blocker_ids: [],
    parent_task_id: null,
    target_date: null,
    created_at: dayAgo.toISOString(),
    updated_at: hourAgo.toISOString(),
    created_by: AGENT_IDS.bePm,
    assigned_to: AGENT_IDS.beDoc,
    claimed_at: dayAgo.toISOString(),
    started_at: dayAgo.toISOString(),
    completed_at: hourAgo.toISOString(),
    self_verified: true,
    qa_verified: true,
    sessions: [],
    branch_name: "docs/TASK-006-api-documentation",
    pr_number: 38,
    pr_url: "https://github.com/roboco/roboco/pull/38",
    plan: null,
    progress_updates: [],
    checkpoints: [],
    commits: [{ hash: "def456789abc", message: "docs: add OpenAPI documentation", timestamp: hourAgo.toISOString(), author_agent_id: AGENT_IDS.beDoc }],
    dev_notes: "Used FastAPI's built-in OpenAPI support.",
    qa_notes: "Documentation is complete and accurate.",
    auditor_notes: null,
    quick_context: null,
  },
];

// =============================================================================
// MOCK DASHBOARD DATA - Matching backend schemas exactly
// =============================================================================

export const mockDashboardStats = {
  total_tasks: mockTasks.length,
  tasks_in_progress: mockTasks.filter((t) => t.status === TaskStatus.IN_PROGRESS).length,
  tasks_blocked: mockTasks.filter((t) => t.status === TaskStatus.BLOCKED).length,
  tasks_completed_today: 1,
  active_agents: 3,
};

// Team health - matches backend: [BACKEND, FRONTEND, UX_UI, MARKETING, BOARD]
export const mockTeamHealth = [
  { team: Team.BACKEND, status: "ok" as const, active_tasks: 3, blocked_tasks: 1, blocked_ratio: 0.33, completed_this_week: 2 },
  { team: Team.FRONTEND, status: "ok" as const, active_tasks: 2, blocked_tasks: 0, blocked_ratio: 0, completed_this_week: 1 },
  { team: Team.UX_UI, status: "slow" as const, active_tasks: 1, blocked_tasks: 0, blocked_ratio: 0, completed_this_week: 0 },
  { team: Team.MARKETING, status: "ok" as const, active_tasks: 0, blocked_tasks: 0, blocked_ratio: 0, completed_this_week: 0 },
  { team: Team.BOARD, status: "ok" as const, active_tasks: 2, blocked_tasks: 0, blocked_ratio: 0, completed_this_week: 1 },
];

// Recent activity - matching /dashboard/activity/recent response
// Use getter to ensure fresh timestamps on each access
export const getMockRecentActivity = () => [
  { id: mockId(), type: "task_update", timestamp: getMinutesAgo(5).toISOString(), agent_id: AGENT_IDS.beDev1, task_id: TASK_IDS.task1, title: "Implement user authentication flow", status: "in_progress", action: "started" },
  { id: mockId(), type: "task_update", timestamp: getMinutesAgo(15).toISOString(), agent_id: AGENT_IDS.feDev1, task_id: TASK_IDS.task3, title: "Fix pagination bug in task list", status: "claimed", action: "claimed" },
  { id: mockId(), type: "task_update", timestamp: getMinutesAgo(30).toISOString(), agent_id: AGENT_IDS.uxDev1, task_id: TASK_IDS.task4, title: "Design new onboarding flow", status: "awaiting_qa", action: "completed" },
  { id: mockId(), type: "task_update", timestamp: getMinutesAgo(45).toISOString(), agent_id: AGENT_IDS.beDev2, task_id: TASK_IDS.task5, title: "Optimize database queries", status: "blocked", action: "blocked" },
  { id: mockId(), type: "task_update", timestamp: getHoursAgo(2).toISOString(), agent_id: AGENT_IDS.beDoc, task_id: TASK_IDS.task6, title: "Write API documentation", status: "completed", action: "passed_qa" },
];
// Legacy export for backwards compatibility
export const mockRecentActivity = getMockRecentActivity();

// Key metrics - matching /dashboard/ceo response
export const mockKeyMetrics = {
  velocity_weekly: 5,
  completion_rate: 0.75,
  documentation_coverage: 0.82,
  active_blockers: 1,
};

// Auditor alerts
export const mockAuditorAlerts = {
  urgent_count: 0,
  warning_count: 1,
  last_report_at: dayAgo.toISOString(),
};

// Roadmap progress
export const mockRoadmapProgress = {
  current_quarter_progress: 0.65,
  high_priority_total: 10,
  high_priority_completed: 6,
};

// =============================================================================
// MOCK ORCHESTRATOR DATA - Matching backend OrchestratorStatusResponse
// =============================================================================

// OrchestratorStatus - matching backend schema exactly
export const mockOrchestratorStatus = {
  total_agents: 19,
  by_state: {
    running: 2,
    ready: 1,
    waiting_long: 1,
    idle: 15,
  },
  waiting_count: 1,
  agents: [
    {
      agent_id: AGENT_IDS.beDev1,
      state: "running",
      task_id: TASK_IDS.task1,
      error_count: 0,
      started_at: hourAgo.toISOString(),
      waiting_for: null,
    },
    {
      agent_id: AGENT_IDS.feDev1,
      state: "ready",
      task_id: TASK_IDS.task3,
      error_count: 0,
      started_at: hourAgo.toISOString(),
      waiting_for: null,
    },
    {
      agent_id: AGENT_IDS.beDev2,
      state: "waiting_long",
      task_id: TASK_IDS.task5,
      error_count: 0,
      started_at: dayAgo.toISOString(),
      waiting_for: "Waiting for task #1 auth implementation",
    },
  ],
};

export const mockWaitingAgents = [
  { agent_id: AGENT_IDS.beDev2, task_id: TASK_IDS.task5, waiting_for: "Waiting for task #1 auth implementation", waiting_since: hourAgo.toISOString(), context: { blocker_task_id: TASK_IDS.task1 } },
];

// =============================================================================
// MOCK CHANNELS - Matching backend ChannelResponse schema
// =============================================================================

export const mockChannels = [
  { id: CHANNEL_IDS.backendCell, name: "Backend Cell", slug: "backend-cell", type: ChannelType.CELL, description: "Backend development team channel", topic: null, member_count: 6, message_count: 150, group_count: 3, is_archived: false, is_private: false, can_write: true },
  { id: CHANNEL_IDS.frontendCell, name: "Frontend Cell", slug: "frontend-cell", type: ChannelType.CELL, description: "Frontend development team channel", topic: null, member_count: 6, message_count: 120, group_count: 2, is_archived: false, is_private: false, can_write: true },
  { id: CHANNEL_IDS.uxuiCell, name: "UX/UI Cell", slug: "uxui-cell", type: ChannelType.CELL, description: "UX/UI design team channel", topic: null, member_count: 5, message_count: 80, group_count: 2, is_archived: false, is_private: false, can_write: true },
  { id: CHANNEL_IDS.devAll, name: "All Developers", slug: "dev-all", type: ChannelType.CROSS_CELL, description: "Cross-cell developer discussion", topic: null, member_count: 10, message_count: 200, group_count: 5, is_archived: false, is_private: false, can_write: true },
  { id: CHANNEL_IDS.announcements, name: "Announcements", slug: "announcements", type: ChannelType.SPECIAL, description: "Company-wide announcements", topic: null, member_count: 19, message_count: 25, group_count: 1, is_archived: false, is_private: false, can_write: false },
];

// =============================================================================
// MOCK NOTIFICATIONS - Matching backend NotificationResponse schema
// =============================================================================

export const mockNotifications = [
  {
    id: mockId(),
    type: NotificationType.TASK_ASSIGNMENT,
    priority: NotificationPriority.NORMAL,
    from_agent: AGENT_IDS.bePm,
    to_agents: [AGENT_IDS.beDev1],
    subject: "New Task Assigned: Implement user authentication flow",
    body: "You have been assigned a new high-priority task. Please review and start work.",
    requires_ack: false,
    is_acknowledged: false,
    is_fully_acknowledged: false,
    is_read: true,
    related_task_id: TASK_IDS.task1,
    timestamp: dayAgo.toISOString(),
    expires_at: null,
  },
  {
    id: mockId(),
    type: NotificationType.BLOCKER_ESCALATION,
    priority: NotificationPriority.HIGH,
    from_agent: AGENT_IDS.beDev2,
    to_agents: [AGENT_IDS.bePm, AGENT_IDS.mainPm],
    subject: "Task Blocked: Optimize database queries",
    body: "Task is blocked waiting for authentication implementation to complete.",
    requires_ack: true,
    is_acknowledged: false,
    is_fully_acknowledged: false,
    is_read: false,
    related_task_id: TASK_IDS.task5,
    timestamp: hourAgo.toISOString(),
    expires_at: null,
  },
  {
    id: mockId(),
    type: NotificationType.REVIEW_REQUEST,
    priority: NotificationPriority.NORMAL,
    from_agent: AGENT_IDS.uxDev1,
    to_agents: [AGENT_IDS.uxQa],
    subject: "QA Review Requested: Design new onboarding flow",
    body: "The onboarding flow design is complete and ready for QA review.",
    requires_ack: true,
    is_acknowledged: false,
    is_fully_acknowledged: false,
    is_read: false,
    related_task_id: TASK_IDS.task4,
    timestamp: hourAgo.toISOString(),
    expires_at: null,
  },
  {
    id: mockId(),
    type: NotificationType.BROADCAST,
    priority: NotificationPriority.NORMAL,
    from_agent: AGENT_IDS.mainPm,
    to_agents: mockAgents.map(a => a.id),
    subject: "Weekly Standup Tomorrow",
    body: "Reminder: Weekly standup meeting tomorrow at 10:00 AM.",
    requires_ack: false,
    is_acknowledged: false,
    is_fully_acknowledged: false,
    is_read: false,
    related_task_id: null,
    timestamp: hourAgo.toISOString(),
    expires_at: null,
  },
];

// =============================================================================
// MOCK JOURNALS - Matching backend JournalResponse/JournalEntryResponse schemas
// =============================================================================

export const mockJournals = [
  {
    id: mockId(),
    agent_id: AGENT_IDS.beDev1,
    total_entries: 5,
    last_entry_at: hourAgo.toISOString(),
    latest_summary: "Working on auth implementation. Good progress on database schema and service layer.",
    summary_updated_at: hourAgo.toISOString(),
    entries_by_type: { [JournalEntryType.TASK_REFLECTION]: 2, [JournalEntryType.DECISION_LOG]: 1, [JournalEntryType.LEARNING]: 2 },
    created_at: weekAgo.toISOString(),
    updated_at: hourAgo.toISOString(),
  },
];

export const mockJournalEntries = [
  {
    id: mockId(),
    journal_id: mockJournals[0].id,
    type: JournalEntryType.TASK_REFLECTION,
    title: "Auth Implementation Progress",
    content: "Completed the database migrations and basic auth service. JWT implementation is clean and follows best practices.",
    task_id: TASK_IDS.task1,
    session_id: null,
    timestamp: hourAgo.toISOString(),
    tags: ["auth", "database", "jwt"],
    sentiment: "positive",
    is_private: false,
    created_at: hourAgo.toISOString(),
    updated_at: null,
  },
  {
    id: mockId(),
    journal_id: mockJournals[0].id,
    type: JournalEntryType.DECISION_LOG,
    title: "Chose bcrypt over argon2",
    content: "Decided to use bcrypt for password hashing. While argon2 is newer, bcrypt is more widely supported and battle-tested.",
    task_id: TASK_IDS.task1,
    session_id: null,
    timestamp: dayAgo.toISOString(),
    tags: ["auth", "security", "decision"],
    sentiment: "neutral",
    is_private: false,
    created_at: dayAgo.toISOString(),
    updated_at: null,
  },
];

// =============================================================================
// MOCK KANBAN BOARDS - Matching backend KanbanBoard schema
// =============================================================================

export const mockKanbanDevBoard = {
  id: mockId(),
  title: "Backend Development",
  board_type: "dev",
  team: Team.BACKEND,
  columns: [
    { id: mockId(), title: "Backlog", status: TaskStatus.PENDING, cards: [{ id: TASK_IDS.task2, title: "Build dashboard components", priority: 2, status: TaskStatus.PENDING, assignee_name: null, is_blocked: false }], card_count: 1 },
    { id: mockId(), title: "In Progress", status: TaskStatus.IN_PROGRESS, cards: [{ id: TASK_IDS.task1, title: "Implement user authentication flow", priority: 1, status: TaskStatus.IN_PROGRESS, assignee_name: "Backend Developer 1", is_blocked: false }], card_count: 1 },
    { id: mockId(), title: "Blocked", status: TaskStatus.BLOCKED, cards: [{ id: TASK_IDS.task5, title: "Optimize database queries", priority: 1, status: TaskStatus.BLOCKED, assignee_name: "Backend Developer 2", is_blocked: true }], card_count: 1 },
    { id: mockId(), title: "Done", status: TaskStatus.COMPLETED, cards: [{ id: TASK_IDS.task6, title: "Write API documentation", priority: 3, status: TaskStatus.COMPLETED, assignee_name: "Backend Documenter", is_blocked: false }], card_count: 1 },
  ],
  total_cards: 4,
  blocked_count: 1,
};

// =============================================================================
// MOCK SESSIONS - Matching backend SessionResponse schema
// =============================================================================

export const mockSessions = [
  {
    id: mockId(),
    group_id: mockId(),
    status: SessionStatus.ACTIVE,
    scope: SessionScope.TASK,
    message_count: 25,
    total_content_length: 5000,
    started_at: hourAgo.toISOString(),
    last_activity_at: now.toISOString(),
    closed_at: null,
  },
  {
    id: mockId(),
    group_id: mockId(),
    status: SessionStatus.CLOSED,
    scope: SessionScope.CELL,
    message_count: 50,
    total_content_length: 12000,
    started_at: dayAgo.toISOString(),
    last_activity_at: new Date(now.getTime() - 2 * 60 * 60 * 1000).toISOString(),
    closed_at: new Date(now.getTime() - 2 * 60 * 60 * 1000).toISOString(),
  },
];

// =============================================================================
// MOCK MESSAGES - Matching backend MessageResponse schema
// =============================================================================

const messageGroupId = mockId();

// Use getter for fresh timestamps
export const getMockMessages = () => [
  {
    id: mockId(),
    agent_id: AGENT_IDS.beDev1,
    channel_id: CHANNEL_IDS.backendCell,
    group_id: messageGroupId,
    session_id: mockSessions[0].id,
    type: MessageType.DIALOGUE,
    content: "Just finished the auth service implementation. Ready to start on the API endpoints.",
    content_length: 78,
    is_reply: false,
    reply_to: null,
    mentions: [],
    task_id: TASK_IDS.task1,
    commit_ref: null,
    timestamp: getMinutesAgo(60).toISOString(),
    edited_at: null,
    was_edited: false,
  },
  {
    id: mockId(),
    agent_id: AGENT_IDS.bePm,
    channel_id: CHANNEL_IDS.backendCell,
    group_id: messageGroupId,
    session_id: mockSessions[0].id,
    type: MessageType.DECISION,
    content: "Great progress! Let's prioritize the OAuth integration next.",
    content_length: 58,
    is_reply: true,
    reply_to: null,
    mentions: [AGENT_IDS.beDev1],
    task_id: TASK_IDS.task1,
    commit_ref: null,
    timestamp: getMinutesAgo(55).toISOString(),
    edited_at: null,
    was_edited: false,
  },
  {
    id: mockId(),
    agent_id: AGENT_IDS.beDev2,
    channel_id: CHANNEL_IDS.backendCell,
    group_id: messageGroupId,
    session_id: mockSessions[0].id,
    type: MessageType.BLOCKER,
    content: "I'm blocked on the database optimization task. Need the auth changes to merge first.",
    content_length: 82,
    is_reply: false,
    reply_to: null,
    mentions: [AGENT_IDS.beDev1],
    task_id: TASK_IDS.task5,
    commit_ref: null,
    timestamp: getMinutesAgo(50).toISOString(),
    edited_at: null,
    was_edited: false,
  },
  {
    id: mockId(),
    agent_id: AGENT_IDS.beDev1,
    channel_id: CHANNEL_IDS.backendCell,
    group_id: messageGroupId,
    session_id: mockSessions[0].id,
    type: MessageType.TECHNICAL,
    content: "Commit pushed: feat(auth): add user authentication schema",
    content_length: 55,
    is_reply: false,
    reply_to: null,
    mentions: [],
    task_id: TASK_IDS.task1,
    commit_ref: "abc123def456",
    timestamp: getMinutesAgo(45).toISOString(),
    edited_at: null,
    was_edited: false,
  },
];
// Legacy export for backwards compatibility
export const mockMessages = getMockMessages();

// =============================================================================
// MOCK GROUPS - Matching backend GroupResponse schema
// =============================================================================

export const mockGroups = [
  {
    id: mockId(),
    name: "General Discussion",
    hierarchy_level: 0,
    is_active: true,
    total_messages: 150,
    active_session_id: mockSessions[0].id,
  },
  {
    id: mockId(),
    name: "Tech Talk",
    hierarchy_level: 1,
    is_active: true,
    total_messages: 80,
    active_session_id: null,
  },
];

// =============================================================================
// MOCK AUDITOR DATA - Matching backend dashboard.py schemas
// =============================================================================

export const mockAuditorFlags = [
  {
    id: mockId(),
    severity: FlagSeverity.WARNING,
    category: "blocked",
    title: "Task blocked for extended period",
    description: "Task 'Optimize database queries' has been blocked for over 24 hours.",
    related_task_id: TASK_IDS.task5,
    related_agent_id: AGENT_IDS.beDev2,
    created_at: hourAgo.toISOString(),
    resolved_at: null,
    notes: null,
  },
  {
    id: mockId(),
    severity: FlagSeverity.INFO,
    category: "process",
    title: "QA queue growing",
    description: "Multiple tasks awaiting QA review. Consider allocating additional QA resources.",
    related_task_id: null,
    related_agent_id: null,
    created_at: dayAgo.toISOString(),
    resolved_at: null,
    notes: null,
  },
];

export const mockAuditorReports = [
  {
    id: mockId(),
    report_type: "daily",
    title: "Daily Status Report - " + now.toLocaleDateString(),
    summary: "Overall team productivity is good. One blocker identified in backend team.",
    sections: [
      { title: "Completed Tasks", content: "1 task completed (API documentation)" },
      { title: "In Progress", content: "2 tasks in progress (auth, bug fix)" },
      { title: "Blockers", content: "1 task blocked (database optimization)" },
    ],
    created_at: hourAgo.toISOString(),
    sent_at: null,
  },
];

export const mockAuditorDashboard = {
  live_feeds: [
    { id: CHANNEL_IDS.backendCell, name: "Backend Cell", status: "streaming", last_activity: now.toISOString(), message_count_24h: 25 },
    { id: CHANNEL_IDS.frontendCell, name: "Frontend Cell", status: "idle", last_activity: hourAgo.toISOString(), message_count_24h: 15 },
    { id: CHANNEL_IDS.uxuiCell, name: "UX/UI Cell", status: "idle", last_activity: dayAgo.toISOString(), message_count_24h: 5 },
  ],
  flagged_items: mockAuditorFlags,
  metrics: {
    total_flags: 2,
    unresolved_flags: 2,
    tasks_reviewed_today: 1,
    avg_review_time_hours: 2.5,
  },
  audit_queue: [
    { type: "qa_review", title: "Design new onboarding flow", task_id: TASK_IDS.task4, team: Team.UX_UI },
  ],
  recent_reports: mockAuditorReports,
};

// Mock mode: dev = mock, production = real backend
export const isMockMode = () => process.env.NODE_ENV === "development";
