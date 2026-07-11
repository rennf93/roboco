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
  AWAITING_PR_REVIEW = "awaiting_pr_review",
  AWAITING_PM_REVIEW = "awaiting_pm_review",
  AWAITING_CEO_APPROVAL = "awaiting_ceo_approval",
  COMPLETED = "completed",
  CANCELLED = "cancelled",
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
  PR_REVIEWER = "pr_reviewer",
  MAIN_PM = "main_pm",
  CELL_PM = "cell_pm",
  DEVELOPER = "developer",
  QA = "qa",
  DOCUMENTER = "documenter",
  PROMPTER = "prompter",
  SECRETARY = "secretary",
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
  OLLAMA_CLOUD = "ollama_cloud",
  OPENAI = "openai",
  LOCAL = "local",
  GROK = "grok",
}

export enum AssignmentScope {
  GLOBAL = "global",
  ROLE = "role",
  AGENT_SLUG = "agent_slug",
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
  // Server-derived architectural constraints (project baseline conventions),
  // moved out of description. Null when conventions are flag-off / none /
  // pre-migration. Read-only on the panel — system-derived, not human-edited.
  constraints?: string | null;
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
  // MegaTask grouping: set on the umbrella (parent_task_id null) and every
  // root-subtask of a batch. null on ordinary tasks.
  batch_id?: string | null;
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
  project_id: string | null; // null for a fan-out task that carries product_id
  product_id?: string | null;
  work_session_id?: string | null;
  // PR Tracking (parallel execution in awaiting_documentation)
  docs_complete: boolean;
  pr_created: boolean;
  // True once PO + Head of Marketing have both reviewed a pending board task.
  // Gates the CEO's Approve & Start button (the task stays pending throughout).
  board_review_complete?: boolean;
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
  pr_reviewer_notes?: string | null;
  doc_notes?: string | null;
  quick_context: string | null;
  // Structured content (source of truth; the *_notes fields are rendered mirrors)
  notes_structured?: Record<string, unknown> | null;
  orchestration_markers?: Record<string, unknown> | null;
  // Review Status
  self_verified: boolean;
  qa_verified: boolean | null;
  // Bounces into needs_revision (task-header "bounced xN" chip). Optional:
  // summaries and mocks omit it.
  revision_count?: number;
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
  project_id?: string; // Repo this task targets; omit for a fan-out task that sets product_id
  product_id?: string; // Cell→project map; drives per-cell routing of delegated subtasks
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
  // Only set on the spawn response: true when the spawn was a no-op because
  // the agent was already active (see SpawnAgentResponse on the backend).
  already_running?: boolean;
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

export interface SessionTaskInfo {
  task_id: string;
  task_title: string | null;
  is_primary: boolean;
  relationship_type: string;
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

export interface AuditorDashboard {
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
  DOCUMENTATION = "documentation",
  CONVERSATIONS = "conversations",
  JOURNALS = "journals",
  ERRORS = "errors",
  STANDARDS = "standards",
  DECISIONS = "decisions",
  REVIEWS = "reviews",
  LEARNINGS = "learnings",
  PLAYBOOKS = "playbooks",
  VAULT_NOTES = "vault_notes",
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
  category:
    | "error_handling"
    | "performance"
    | "testing"
    | "pattern"
    | "tool"
    | "other";
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
  score: number;
  index_type: string;
  metadata: Record<string, unknown>;
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
  quality_command: string | null;
  // Autonomous maintenance opt-in
  ci_watch_enabled: boolean;
  ci_watch_workflow: string | null;
  video_engine_enabled: boolean;
  dep_update_command: string | null;
  dep_update_paths: string[] | null;
  sandbox_services: string[] | null;
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
  quality_command?: string;
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
  quality_command?: string;
  // Autonomous maintenance opt-in
  ci_watch_enabled?: boolean;
  ci_watch_workflow?: string;
  video_engine_enabled?: boolean;
  dep_update_command?: string;
  dep_update_paths?: string[];
  sandbox_services?: string[];
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
  video_engine_enabled: boolean;
}

export interface ProductCellMapping {
  team: Team;
  project_id: string;
}

export interface Product {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  cells: ProductCellMapping[];
  created_by: string;
  created_at: string;
  updated_at: string | null;
}

export interface ProductSummary {
  id: string;
  name: string;
  slug: string;
  cell_count: number;
}

export interface ProductCreate {
  name: string;
  slug: string;
  description?: string;
  cells?: ProductCellMapping[];
}

export interface ProductUpdate {
  name?: string;
  description?: string;
  cells?: ProductCellMapping[];
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

// =============================================================================
// TOKEN USAGE TYPES  (aligned to real backend: GET /api/usage/*)
// =============================================================================

/** Aggregated token and cost totals — GET /usage/summary?period=24h|7d|30d */
export interface UsageSummary {
  tokens_input: number;
  tokens_output: number;
  total_tokens: number;
  total_cost_usd: number;
  trend_pct: number;
  period: string;
}

/** Per-agent usage row — GET /usage/by-agent?period=24h|7d|30d */
export interface AgentUsageRow {
  agent_slug: string;
  tokens_input: number;
  tokens_output: number;
  total_tokens: number;
  cost_usd: number;
  pct_of_total: number;
}

/** Per-team usage row — GET /usage/by-team?period=24h|7d|30d */
export interface TeamUsageRow {
  team: string;
  tokens_input: number;
  tokens_output: number;
  total_tokens: number;
  cost_usd: number;
  pct_of_total: number;
}

/** Per-model usage slice — GET /usage/by-model?period=24h|7d|30d */
export interface ModelUsageSlice {
  model: string;
  tokens_input: number;
  tokens_output: number;
  total_tokens: number;
  cost_usd: number;
  pct_of_total: number;
}

/** One data point in a token-usage time series — GET /usage/time-series?period=24h|7d|30d
 *
 * - 24h → hourly buckets; 7d / 30d → daily buckets
 * - bucket is an ISO datetime string (from PostgreSQL date_trunc)
 */
export interface UsageTimePoint {
  bucket: string;
  tokens_input: number;
  tokens_output: number;
  total_tokens: number;
  cost_usd: number;
}

/** Monthly cost projection — GET /usage/projection */
export interface UsageProjection {
  total_cost_7d: number;
  avg_daily_cost_usd: number;
  projected_monthly_cost_usd: number;
  basis_days: number;
}

/** Cache efficiency stats — GET /usage/cache-efficiency?period=24h|7d|30d */
export interface CacheEfficiencyResponse {
  cache_hit_rate: number;
  tokens_cache_read: number;
  tokens_cache_write: number;
  tokens_input: number;
  cost_saved_by_cache_usd: number;
  period: string;
}

/** Per-role usage row (with cache hit rate) — GET /usage/by-role?period=24h|7d|30d */
export interface RoleUsageRow {
  role: string;
  tokens_input: number;
  tokens_output: number;
  tokens_cache_read: number;
  tokens_cache_write: number;
  cache_hit_rate: number;
  total_tokens: number;
  cost_usd: number;
  pct_of_total: number;
}

/** One per-role row in the spawn-waste signal */
export interface RoleWasteRow {
  role: string;
  spawns: number;
  unproductive: number;
  unproductive_pct: number;
}

/** One wedged agent/task pair the circuit breaker is counting */
export interface RespawnStrikeRow {
  agent_slug: string;
  task_id: string;
  count: number;
  last_status: string | null;
  notified: boolean;
}

/** Spawn-churn signal — GET /usage/spawn-waste?period=24h|7d|30d */
export interface SpawnWasteResponse {
  total_spawns: number;
  unproductive_spawns: number;
  unproductive_pct: number;
  by_role: RoleWasteRow[];
  respawn_strikes: RespawnStrikeRow[];
  /** Counting scope — non-Anthropic transcripts don't populate output tokens. */
  basis?: string;
  period: string;
}

/** Individual inference session for the sessions table (mock-mode only — no real backend endpoint) */
export interface UsageSession {
  id: string;
  agent_slug: string;
  started_at: string;
  ended_at: string | null;
  tokens_input: number;
  tokens_output: number;
  tokens_cache: number;
  total_tokens: number;
  cost: number;
  model: string;
}

// =============================================================================
// Observability (0.10.0): cycle-time, bottlenecks, rework, scorecard
// =============================================================================

export interface StageTiming {
  status: string;
  avg_seconds: number;
  median_seconds: number;
  p90_seconds: number;
  sample_size: number;
}

export interface StageBottleneck {
  status: string;
  cumulative_seconds: number;
  parked_now: number;
  pct_of_total: number;
}

export interface BottleneckReport {
  by_stage: StageBottleneck[];
  worst_stage: string | null;
  active_blockers: number;
}

export interface AgentReworkRate {
  agent_slug: string;
  rate: number;
  qa_fails: number;
  pr_fails: number;
  pm_rejects: number;
  ceo_rejects: number;
}

export interface TeamReworkRate {
  team: string;
  rate: number;
}

export interface ReworkReport {
  rate: number;
  total_completed: number;
  total_reworked: number;
  by_team: TeamReworkRate[];
  by_agent: AgentReworkRate[];
  rework_cost_usd: number;
}

export interface Scorecard {
  scope: string;
  id: string;
  name: string;
  tasks_completed: number;
  avg_cycle_hours: number | null;
  rework_rate: number;
  tokens: number;
  cost_usd: number;
}

// --- Granular per-member metrics (v0.15.0) ---

export interface StageEffort {
  status: string;
  active_seconds: number;
  wait_seconds: number;
}

export interface TaskMetrics {
  task_id: string;
  active_runtime_seconds: number;
  wall_clock_seconds: number;
  turns: number;
  tool_calls: number;
  tokens: number;
  cost_usd: number;
  revision_count: number;
  qa_fails: number;
  pr_fails: number;
  pm_rejects: number;
  ceo_rejects: number;
  stints: number;
  stages: StageEffort[];
  findings_open: number;
  findings_total: number;
}

export interface MemberScorecard {
  scope: string;
  id: string;
  name: string;
  member_kind: "agent";
  tasks_completed: number;
  first_pass_yield: number | null;
  effort_throughput_per_hour: number | null;
  active_runtime_hours: number;
  turns: number;
  tool_calls: number;
  tokens: number;
  cost_usd: number;
  turns_per_task: number | null;
  tool_calls_per_task: number | null;
  revisions_caused: number;
  revisions_received: number;
  qa_pass_rate: number | null;
  escalations: number;
  blocked_others: number;
  idle_hours: number;
  utilization: number | null;
  includes_live_inflight: boolean;
}

export interface OrgScorecard {
  scope: string;
  team: string | null;
  member_count: number;
  tasks_completed: number;
  first_pass_yield: number | null;
  effort_throughput_per_hour: number | null;
  active_runtime_hours: number;
  turns: number;
  tool_calls: number;
  tokens: number;
  cost_usd: number;
  revisions_caused: number;
  revisions_received: number;
}

export interface CeoScorecard {
  member_kind: "ceo";
  approval_p50_seconds: number;
  approval_p90_seconds: number;
  approval_count: number;
  unblock_p50_seconds: number;
  unblock_count: number;
  godmode_actions: number;
}
