// =============================================================================
// ENUMS (matching backend models/base.py)
// =============================================================================

export enum TaskStatus {
  BACKLOG = "backlog",
  PENDING = "pending",
  CLAIMED = "claimed",
  IN_PROGRESS = "in_progress",
  BLOCKED = "blocked",
  PAUSED = "paused",
  VERIFYING = "verifying",
  NEEDS_REVISION = "needs_revision",
  AWAITING_QA = "awaiting_qa",
  AWAITING_DOCUMENTATION = "awaiting_documentation",
  AWAITING_PM_REVIEW = "awaiting_pm_review",
  AWAITING_CEO_APPROVAL = "awaiting_ceo_approval",
  COMPLETED = "completed",
  CANCELLED = "cancelled",
  QUARANTINED = "quarantined", // Special state for problematic tasks
}

export enum Team {
  BOARD = "board",
  MAIN_PM = "main_pm",
  BACKEND = "backend",
  FRONTEND = "frontend",
  UX_UI = "ux_ui",
  MARKETING = "marketing",
}

export enum AgentRole {
  SYSTEM = "system",
  CEO = "ceo",
  PRODUCT_OWNER = "product_owner",
  HEAD_MARKETING = "head_marketing",
  AUDITOR = "auditor",
  MAIN_PM = "main_pm",
  CELL_PM = "cell_pm",
  DEVELOPER = "developer",
  QA = "qa",
  DOCUMENTER = "documenter",
}

export enum AgentState {
  IDLE = "idle",
  STARTING = "starting",
  READY = "ready",
  RUNNING = "running",
  WAITING_LONG = "waiting_long",
  ERROR = "error",
  STOPPED = "stopped",
  TERMINATED = "terminated",
}

export enum Priority {
  LOW = "low",
  MEDIUM = "medium",
  HIGH = "high",
  URGENT = "urgent",
}

export enum Complexity {
  LOW = "low",
  MEDIUM = "medium",
  HIGH = "high",
}

export enum TaskNature {
  TECHNICAL = "technical",
  NON_TECHNICAL = "non_technical",
}

export enum TaskType {
  CODE = "code", // Technical - full git workflow
  DOCUMENTATION = "documentation", // May or may not need git
  RESEARCH = "research", // No git
  PLANNING = "planning", // No git
  DESIGN = "design", // No git
  ADMINISTRATIVE = "administrative", // No git
}

export enum SubstituteReason {
  LOW_CONTEXT = "low_context", // Insufficient context to continue safely
  OUT_OF_SCOPE_TEAM = "out_of_scope_team", // Task belongs to different team
  OUT_OF_SCOPE_ROLE = "out_of_scope_role", // Task requires different role
  TASK_COMPLETE = "task_complete", // Finished work, releasing task
  MAX_RETRIES = "max_retries", // Exceeded retry limit, need fresh perspective
  BLOCKED_EXTERNAL = "blocked_external", // Need skills outside agent's capabilities
}

export enum HandoffStatus {
  PENDING = "pending",
  CLAIMED = "claimed",
  IN_PROGRESS = "in_progress",
  ACCEPTED = "accepted",
  COMPLETED = "completed",
}

export enum ModelProvider {
  ANTHROPIC = "anthropic",
  OPENAI = "openai",
  LOCAL = "local",
}

export enum SessionTaskRelationshipType {
  DISCUSSION = "discussion",
  PLANNING = "planning",
  REVIEW = "review",
  RETROSPECTIVE = "retrospective",
}

export enum NotificationType {
  TASK_ASSIGNMENT = "task_assignment",
  PRIORITY_CHANGE = "priority_change",
  BLOCKER_ESCALATION = "blocker_escalation",
  REVIEW_REQUEST = "review_request",
  DOCUMENTATION_REQUEST = "documentation_request",
  ALERT = "alert",
  BROADCAST = "broadcast",
  KNOWLEDGE_SHARE = "knowledge_share", // Cross-agent learning notification
  MENTION = "mention", // @mention in chat
}

export enum NotificationPriority {
  NORMAL = "normal",
  HIGH = "high",
  URGENT = "urgent",
}

export enum SessionStatus {
  ACTIVE = "active",
  CLOSED = "closed",
  TIMED_OUT = "timed_out",
}

export enum SessionScope {
  INITIATIVE = "initiative",
  CELL = "cell",
  TASK = "task",
}

export enum MessageType {
  REASONING = "reasoning",
  DIALOGUE = "dialogue",
  DECISION = "decision",
  ACTION = "action",
  BLOCKER = "blocker",
  TECHNICAL = "technical",
}

export enum ChannelType {
  CELL = "cell",
  CROSS_CELL = "cross_cell",
  MANAGEMENT = "management",
  SPECIAL = "special",
}

export enum AgentStatus {
  ACTIVE = "active",
  IDLE = "idle",
  OFFLINE = "offline",
}

// =============================================================================
// TASK SUPPORTING TYPES (matching backend schemas/tasks.py)
// =============================================================================

export interface ProgressUpdate {
  timestamp: string;
  agent_id: string;
  message: string;
  percentage: number | null;
}

export interface Checkpoint {
  id: string;
  timestamp: string;
  agent_id: string;
  state_summary: string;
  remaining_work: string[];
  notes: string | null;
}

export interface CommitRef {
  hash: string;
  message: string;
  timestamp: string;
  author_agent_id: string | null;
}

export interface DocRef {
  path: string;
  title: string;
  doc_type: string;
  version: string | null;
}

export interface FileRef {
  path: string;
  description: string;
  file_type: string;
  size_bytes: number | null;
}

export interface ExecutionLog {
  events: Record<string, unknown>[];
  errors: Record<string, unknown>[];
  total_duration_seconds: number | null;
}

export interface TaskSessionLink {
  session_id: string;
  channel_slug: string;
  scope: SessionScope;
  is_primary: boolean;
  relationship_type: string;
}

export interface SubTask {
  id: string;
  title: string;
  description: string | null;
  completed: boolean;
  order: number;
  estimated_hours: number | null;
  notes: string | null;
}

export interface TaskPlan {
  approach: string;
  sub_tasks: SubTask[];
  technical_considerations: string[];
  risks: Array<{ description: string; mitigation: string; severity?: string }>;
  open_questions: Array<{
    question: string;
    answer: string | null;
    answered_by: string | null;
    answered_at: string | null;
  }>;
}

// =============================================================================
// CORE TYPES
// =============================================================================

export interface Task {
  id: string;
  title: string;
  description: string;
  acceptance_criteria: string[];
  status: TaskStatus;
  priority: number; // 0=P0(highest), 1=P1, 2=P2, 3=P3(lowest)
  sequence: number; // Order number within siblings
  team: Team;
  created_by: string;
  assigned_to: string | null;
  parent_task_id: string | null;
  dependency_ids: string[];
  blocker_ids: string[];
  created_at: string;
  updated_at: string | null;
  claimed_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  target_date: string | null;
  estimated_complexity: Complexity;
  nature: TaskNature;
  // Task Type & Git Configuration (all tasks follow git workflow)
  task_type: TaskType;
  project_id: string;
  work_session_id?: string | null;
  // PR Tracking (parallel execution in awaiting_documentation)
  docs_complete: boolean;
  pr_created: boolean;
  pm_approvals: Record<string, boolean>;
  // Planning
  plan: TaskPlan | null;
  // Execution
  checkpoints: Checkpoint[];
  progress_updates: ProgressUpdate[];
  execution_log?: ExecutionLog | null;
  // Artifacts
  commits: CommitRef[];
  documents?: DocRef[];
  outputs?: FileRef[];
  // Documentation
  dev_notes: string | null;
  qa_notes: string | null;
  auditor_notes: string | null;
  quick_context: string | null;
  // Review Status
  self_verified: boolean;
  qa_verified: boolean | null;
  // Linked Sessions
  sessions: TaskSessionLink[];
  // Git/Development Context
  branch_name: string | null;
  pr_number: number | null;
  pr_url: string | null;
  // RAG Context
  proactive_context?: Record<string, unknown> | null;
}

export interface TaskCreate {
  title: string;
  description: string;
  acceptance_criteria: string[]; // Required (at least one)
  team: Team;
  priority?: number; // 0-3, defaults to 2
  status?: TaskStatus; // Defaults to PENDING; use BACKLOG for PM setup
  parent_task_id?: string;
  assigned_to?: string; // Agent slug for immediate assignment
  target_date?: string;
  estimated_complexity?: Complexity;
  nature?: TaskNature; // Technical or non-technical work
  // Task ordering and dependencies
  sequence?: number; // Order within siblings (lower = first)
  dependency_ids?: string[]; // Task IDs that must complete first
  // Git configuration (all tasks follow git workflow)
  task_type?: TaskType; // Defaults to CODE
  project_id: string; // Project this task works on (required)
}

// =============================================================================
// TASK LIFECYCLE REQUEST TYPES (matching backend schemas/tasks.py)
// =============================================================================

export interface ProgressRequest {
  message: string;
  percentage?: number; // 0-100
}

export interface CheckpointRequest {
  state_summary: string;
  remaining_work: string[];
  notes?: string;
}

export interface CommitRequest {
  hash: string; // 7-40 chars
  message: string;
}

export interface SoftBlockRequest {
  reason: string;
  blocker_type: string; // "external" | "internal" | "question" | "dependency"
  what_needed: string;
}

export interface EscalateRequest {
  reason: string;
  escalate_to?: string; // Target agent ID (defaults to cell PM)
  block_task?: boolean; // Whether to block the task (default: true)
}

export interface EscalateResponse {
  status: string;
  task_id: string;
  escalated_to: string;
  reason: string;
  message: string;
}

export interface SubstituteRequest {
  reason: SubstituteReason;
  notes?: string;
}

export interface TaskCountResponse {
  counts: Record<string, number>;
}

export interface ModelConfig {
  provider: ModelProvider;
  name: string;
  fallback: string | null;
  temperature: number;
  max_tokens: number;
}

export interface AgentPermissions {
  can_notify: boolean;
  channels_read: string[];
  channels_write: string[];
}

export interface AgentMetrics {
  tasks_completed: number;
  tasks_in_progress: number;
  avg_completion_hours: number | null;
  quality_score: number | null;
  last_active: string | null;
}

export interface Agent {
  id: string;
  agent_id: string;
  slug?: string; // URL-safe identifier
  name: string;
  description?: string | null;
  role: AgentRole;
  team: Team | null;
  cell: string | null;
  status: AgentState;
  // Model configuration
  model?: ModelConfig | null;
  system_prompt?: string | null;
  // Capabilities and permissions
  capabilities?: string[];
  permissions?: AgentPermissions | null;
  // Metrics
  metrics?: AgentMetrics | null;
  // Associations
  current_task_id?: string | null;
  journal_id?: string | null;
  // Timestamps
  created_at?: string;
  updated_at?: string | null;
}

// Lightweight agent for lists
export interface AgentSummary {
  id: string;
  agent_id: string;
  slug: string;
  name: string;
  role: AgentRole;
  team: Team | null;
  status: AgentState;
  current_task_id: string | null;
}

// AgentStatusResponse from backend orchestrator
export interface AgentStatusResponse {
  agent_id: string;
  state: string;
  task_id: string | null;
  error_count: number;
  started_at: string | null;
  waiting_for: string | null;
}

// OrchestratorStatusResponse from backend
export interface OrchestratorStatus {
  total_agents: number;
  by_state: Record<string, number>;
  waiting_count: number;
  agents: AgentStatusResponse[];
}

// WaitingAgentResponse from backend
export interface WaitingAgent {
  agent_id: string;
  task_id: string | null;
  waiting_for: string;
  waiting_since: string;
  context: Record<string, unknown>;
}

export interface Channel {
  id: string;
  name: string;
  slug: string;
  type: ChannelType;
  description: string | null;
  topic: string | null;
  member_count: number;
  message_count: number;
  group_count: number;
  is_archived: boolean;
  is_private: boolean;
  can_write: boolean;
}

export interface ChannelDetail extends Channel {
  groups: Group[];
}

export interface Group {
  id: string;
  name: string;
  hierarchy_level: number;
  is_active: boolean;
  total_messages: number;
  active_session_id: string | null;
}

export interface Message {
  id: string;
  agent_id: string;
  channel_id: string;
  group_id: string;
  session_id: string;
  type: MessageType;
  content: string;
  content_length: number;
  is_reply: boolean;
  reply_to: string | null;
  mentions: string[];
  task_id: string | null;
  commit_ref: string | null;
  timestamp: string;
  edited_at: string | null;
  was_edited: boolean;
}

export interface SessionTaskInfo {
  task_id: string;
  task_title: string | null;
  is_primary: boolean;
  relationship_type: string;
}

export interface Session {
  id: string;
  group_id: string;
  status: SessionStatus;
  scope: SessionScope;
  message_count: number;
  total_content_length: number;
  started_at: string;
  last_activity_at: string;
  closed_at: string | null;
  task_links: SessionTaskInfo[];
}

export interface Notification {
  id: string;
  type: NotificationType;
  priority: NotificationPriority;
  from_agent: string;
  to_agents: string[];
  subject: string;
  body: string;
  requires_ack: boolean;
  is_acknowledged: boolean;
  is_fully_acknowledged: boolean;
  is_read: boolean;
  related_task_id: string | null;
  related_message_ids: string[];
  timestamp: string;
  expires_at: string | null;
  acked_by: string[];
  acked_at: Record<string, string>; // Agent ID -> timestamp
}

export interface KanbanBoard {
  columns: KanbanColumn[];
  total_tasks: number;
}

export interface KanbanColumn {
  id: string;
  title: string;
  status: TaskStatus;
  tasks: Task[];
  count: number;
}

// =============================================================================
// API RESPONSE TYPES
// =============================================================================

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface NotificationListResponse {
  items: Notification[];
  total: number;
  unread_count: number;
  pending_ack_count: number;
}

export interface HealthResponse {
  status: string;
  version: string;
  environment: string;
}

// =============================================================================
// JOURNAL TYPES (matching backend schemas/journals.py)
// =============================================================================

export enum JournalEntryType {
  TASK_REFLECTION = "task_reflection",
  DECISION_LOG = "decision_log",
  LEARNING = "learning",
  STRUGGLE = "struggle",
  GENERAL = "general",
}

export interface Journal {
  id: string;
  agent_id: string;
  total_entries: number;
  last_entry_at: string | null;
  latest_summary: string | null;
  summary_updated_at: string | null;
  entries_by_type: Record<string, number>;
  created_at: string;
  updated_at: string | null;
}

export interface JournalEntry {
  id: string;
  journal_id: string;
  type: JournalEntryType;
  title: string;
  content: string;
  task_id: string | null;
  session_id: string | null;
  timestamp: string;
  tags: string[];
  sentiment: string | null;
  is_private: boolean;
  created_at: string;
  updated_at: string | null;
}

export interface JournalEntryCreate {
  type: JournalEntryType;
  title: string;
  content: string;
  task_id?: string;
  session_id?: string;
  tags?: string[];
  sentiment?: string;
  is_private?: boolean;
}

export interface JournalStats {
  total_entries: number;
  entries_by_type: Record<string, number>;
  last_entry_at: string | null;
  has_summary: boolean;
}

export interface GrowthMetrics {
  total_reflections: number;
  total_learnings: number;
  total_struggles: number;
  total_decisions: number;
  struggle_resolution_rate: number;
  learning_frequency: number;
  sentiment_trend: string;
}

// =============================================================================
// AUDITOR TYPES (matching backend schemas/dashboard.py)
// =============================================================================

export enum FlagSeverity {
  INFO = "info",
  WARNING = "warning",
  URGENT = "urgent",
}

export interface AuditorFlag {
  id: string;
  severity: FlagSeverity;
  category: string;
  title: string;
  description: string;
  related_task_id: string | null;
  related_agent_id: string | null;
  created_at: string;
  resolved_at: string | null;
  notes: string | null;
}

export interface AuditorReport {
  id: string;
  report_type: string;
  title: string;
  summary: string;
  sections: Array<Record<string, unknown>>;
  created_at: string;
  sent_at: string | null;
}

export interface ChannelFeed {
  id: string;
  name: string;
  status: string;
  last_activity: string | null;
  message_count_24h: number;
}

export interface AuditorDashboard {
  live_feeds: ChannelFeed[];
  flagged_items: AuditorFlag[];
  metrics: Record<string, number>;
  audit_queue: Array<{
    type: string;
    title: string;
    task_id: string | null;
    team: string | null;
  }>;
  recent_reports: AuditorReport[];
}

export interface TeamHealth {
  team: Team;
  status: "ok" | "slow" | "critical";
  active_tasks: number;
  blocked_tasks: number;
  blocked_ratio: number;
  completed_this_week: number;
}

export interface CEOOverview {
  health_status: TeamHealth[];
  key_metrics: Record<string, unknown>;
  auditor_alerts: Record<string, unknown>;
  roadmap_progress: Record<string, unknown>;
}

// =============================================================================
// KNOWLEDGE BASE TYPES (matching backend optimal API)
// =============================================================================

export enum KBIndexType {
  CODE = "code",
  DOCUMENTATION = "documentation",
  CONVERSATIONS = "conversations",
  JOURNALS = "journals",
  ERRORS = "errors",
  STANDARDS = "standards",
  DECISIONS = "decisions",
  REVIEWS = "reviews",
  LEARNINGS = "learnings",
}

export interface KBSearchRequest {
  query: string;
  index_types?: KBIndexType[];
  top_k?: number;
  min_score?: number;
}

export interface KBSearchResult {
  content: string;
  source: string;
  score: number;
  index_type: KBIndexType;
  metadata: Record<string, unknown>;
}

export interface KBSearchResponse {
  results: KBSearchResult[];
  total: number;
  query: string;
}

export interface RAGQueryRequest {
  question: string;
  index_types?: KBIndexType[];
  max_context_chunks?: number;
}

export interface RAGCitation {
  content: string;
  source: string;
  score: number;
  index_type: KBIndexType;
  metadata: Record<string, unknown>;
}

export interface RAGQueryResponse {
  answer: string;
  citations: RAGCitation[];
  query: string;
  context_used: number;
}

export interface KBIndexStats {
  index_type: KBIndexType;
  document_count: number;
  chunk_count: number;
  last_updated: string | null;
}

export interface KBStats {
  indexes: KBIndexStats[];
  total_documents: number;
  total_chunks: number;
}

// =============================================================================
// KNOWLEDGE BASE ADVANCED TYPES (mentor, errors, decisions, standards, etc.)
// =============================================================================

// RAG Health
export interface RAGHealthResponse {
  healthy: boolean;
  embedding_status: string;
  llm_status: string;
  vector_store_status: string;
  details: Record<string, unknown>;
}

// Mentor
export interface MentorAskRequest {
  question: string;
  conversation_id?: string;
  domain?: "coding" | "security" | "workflow";
}

export interface MentorAskResponse {
  answer: string;
  sources: KBSearchResult[];
  conversation_id: string;
  suggested_followups: string[];
  search_stats?: Record<string, number>;
  search_errors?: Record<string, string>;
  // Personalization context (what makes mentor different)
  agent_role?: string;
  agent_team?: string;
  journal_entries_used?: number;
}

// Errors
export interface ErrorSearchRequest {
  error_message: string;
  context?: string;
}

export interface ErrorSearchResponse {
  results: KBSearchResult[];
  total: number;
}

export interface ErrorRecordRequest {
  error_message: string;
  context: string;
  solution: string;
  worked?: boolean;
  tags?: string[];
}

export interface ErrorRecordResponse {
  error_id: string;
  status: string;
}

// Decisions
export interface DecisionCheckRequest {
  topic: string;
}

export interface PastDecision {
  topic: string;
  decision: string;
  rationale: string;
  context?: string;
}

export interface DecisionCheckResponse {
  has_precedent: boolean;
  decisions: PastDecision[];
  recommendation: string;
}

export interface DecisionAlternative {
  option: string;
  pros?: string[];
  cons?: string[];
}

export interface DecisionRecordRequest {
  topic: string;
  decision: string;
  rationale: string;
  alternatives?: DecisionAlternative[];
  context?: string;
  scope?: "team" | "org";
  tags?: string[];
}

export interface DecisionRecordResponse {
  decision_id: string;
  status: string;
}

// Standards
export interface StandardsGetRequest {
  domain: "coding" | "security" | "workflow";
  language?: string;
}

export interface StandardsGetResponse {
  standards: KBSearchResult[];
  total: number;
}

export interface ValidateActionRequest {
  action_type: string;
  context: string;
}

export interface ValidationViolation {
  rule: string;
  message: string;
  severity: "error" | "warning";
}

export interface ValidateActionResponse {
  allowed: boolean;
  violations: ValidationViolation[];
  warnings: ValidationViolation[];
  relevant_standards: KBSearchResult[];
}

// Code Review
export interface CodeReviewRequest {
  code: string;
  file_path: string;
  change_type?: "add" | "modify" | "delete";
}

export interface CodeReviewComment {
  line?: number;
  type: "suggestion" | "issue" | "praise";
  message: string;
  severity?: "low" | "medium" | "high";
}

export interface CodeReviewResponse {
  file_path: string;
  approved: boolean;
  score: number;
  comments: CodeReviewComment[];
  standards_checked: string[];
  similar_reviews: string[];
}

// Learnings
export interface LearningRecordRequest {
  content: string;
  category: "error_handling" | "performance" | "testing" | "pattern" | "tool" | "other";
  team?: "backend" | "frontend" | "ux_ui";
  shareable?: boolean;
  tags?: string[];
}

export interface LearningRecordResponse {
  learning_id: string;
  status: string;
}

export interface LearningSearchRequest {
  query: string;
  category?: string;
  team?: string;
  top_k?: number;
}

// Proactive Context
export interface ProactiveContextItem {
  content: string;
  source: string;
  relevance: number;
  type: string;
}

export interface ProactiveContextRequest {
  task_id: string;
}

export interface ProactiveContextResponse {
  task_id?: string;
  agent_id?: string;
  similar_tasks: ProactiveContextItem[];
  relevant_learnings: ProactiveContextItem[];
  code_patterns: ProactiveContextItem[];
  applicable_standards: ProactiveContextItem[];
  recent_decisions: ProactiveContextItem[];
  known_issues: ProactiveContextItem[];
  summary: string;
}

// Token Estimation
export interface TokenEstimateRequest {
  content: string;
  model?: string;
}

export interface TokenEstimateResponse {
  token_count: number;
  model: string;
  content_length: number;
}

// Index Management
export interface RefreshIndexRequest {
  index_type: KBIndexType;
  sources: string[];
}

export interface RefreshIndexResponse {
  status: string;
  index_type: string;
  sources: string[];
}

export interface ClearIndexResponse {
  status: string;
  index_type: string;
}

/**
 * Detailed report of indexing operation results.
 * Provides visibility into what was indexed successfully vs what failed.
 */
export interface IndexingReport {
  index_type: string;
  total_attempted: number;
  successful: number;
  failed: number;
  skipped: number;
  success_rate: number;
  has_failures: boolean;
  failed_sources: [string, string][]; // [source_path, error_message]
  duration_seconds: number;
}

/**
 * Combined report for auto-indexing/reindexing operations.
 */
export interface ReindexResponse {
  status: string;
  code: IndexingReport | null;
  documentation: IndexingReport | null;
  overall_success: boolean;
  warnings: string[];
  // Legacy fields for backwards compatibility
  code_count?: number;
  docs_count?: number;
}

/**
 * Request parameters for reindexing
 */
export interface ReindexRequest {
  force?: boolean;
  timeout_seconds?: number;
}

/**
 * Response for index staleness check
 */
export interface IndexStalenessResponse {
  needs_reindex: boolean;
  stale_indexes: string[];
  details: Record<
    string,
    {
      status: "current" | "stale" | "never_indexed";
      last_indexed: string | null;
      stale_file_count?: number;
      stale_files_sample?: string[];
      indexed_sources_count?: number;
      recommendation?: string;
    }
  >;
}

// =============================================================================
// GIT INTEGRATION TYPES (matching backend schemas/project.py, work_session.py)
// =============================================================================

export enum WorkSessionStatus {
  ACTIVE = "active",
  COMPLETED = "completed",
  ABANDONED = "abandoned",
}

export interface Project {
  id: string;
  name: string;
  slug: string;
  git_url: string;
  default_branch: string;
  protected_branches: string[];
  assigned_cell: Team;
  // Git authentication (token never exposed, only boolean indicator)
  has_git_token: boolean;
  is_active: boolean;
  // CI/CD commands
  test_command: string | null;
  lint_command: string | null;
  format_command: string | null;
  typecheck_command: string | null;
  build_command: string | null;
  // Runtime state
  workspace_path: string | null;
  last_synced_at: string | null;
  head_commit: string | null;
  // Metadata
  created_by: string;
  created_at: string;
  updated_at: string | null;
}

export interface ProjectCreate {
  name: string;
  slug: string;
  git_url: string;
  default_branch?: string;
  protected_branches?: string[];
  assigned_cell: Team;
  // Git authentication (stored encrypted, never returned)
  git_token?: string;
  test_command?: string;
  lint_command?: string;
  format_command?: string;
  typecheck_command?: string;
  build_command?: string;
}

export interface ProjectUpdate {
  name?: string;
  git_url?: string;
  default_branch?: string;
  protected_branches?: string[];
  assigned_cell?: Team;
  // Git authentication (empty string clears, undefined leaves unchanged)
  git_token?: string;
  is_active?: boolean;
  test_command?: string;
  lint_command?: string;
  format_command?: string;
  typecheck_command?: string;
  build_command?: string;
}

export interface ProjectSummary {
  id: string;
  name: string;
  slug: string;
  git_url: string;
  assigned_cell: Team;
  is_active: boolean;
  has_workspace: boolean;
  has_git_token: boolean;
}

export interface WorkSession {
  id: string;
  project_id: string;
  task_id: string;
  agent_id: string;
  // Branch management
  branch_name: string;
  base_branch: string;
  target_branch: string;
  // Lifecycle
  started_at: string;
  ended_at: string | null;
  status: WorkSessionStatus;
  // Audit trail
  commits: string[];
  files_modified: string[];
  // PR tracking
  pr_number: number | null;
  pr_url: string | null;
  pr_status: string | null;
  pr_created_at: string | null;
  pr_merged_at: string | null;
  merged_by: string | null;
  // Timestamps
  created_at: string;
  updated_at: string;
}

export interface WorkSessionSummary {
  id: string;
  task_id: string;
  branch_name: string;
  status: WorkSessionStatus;
  started_at: string;
  has_pr: boolean;
}

export interface WorkSessionCreate {
  project_id: string;
  task_id: string;
  branch_name: string;
  base_branch: string;
  target_branch: string;
}

// =============================================================================
// CEO APPROVAL TYPES
// =============================================================================

export interface CEOApprovalRequest {
  notes?: string;
}

export interface CEORejectRequest {
  notes: string; // Required for rejection
}
