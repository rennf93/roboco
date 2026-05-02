/**
 * Knowledge Base API Client
 *
 * Semantic search and RAG queries against indexed knowledge.
 * Matches backend: roboco/api/routes/optimal.py
 */

import api from "./client";
import {
  KBIndexType,
  type KBSearchRequest,
  type KBSearchResponse,
  type KBSearchResult,
  type RAGQueryRequest,
  type RAGQueryResponse,
  type KBStats,
  type KBIndexStats,
  type RAGHealthResponse,
  type MentorAskRequest,
  type MentorAskResponse,
  type ErrorSearchRequest,
  type ErrorSearchResponse,
  type ErrorRecordRequest,
  type ErrorRecordResponse,
  type DecisionCheckRequest,
  type DecisionCheckResponse,
  type DecisionRecordRequest,
  type DecisionRecordResponse,
  type StandardsGetRequest,
  type StandardsGetResponse,
  type ValidateActionRequest,
  type ValidateActionResponse,
  type CodeReviewRequest,
  type CodeReviewResponse,
  type LearningRecordRequest,
  type LearningRecordResponse,
  type LearningSearchRequest,
  type ProactiveContextRequest,
  type ProactiveContextResponse,
  type TokenEstimateRequest,
  type TokenEstimateResponse,
  type RefreshIndexRequest,
  type RefreshIndexResponse,
  type ClearIndexResponse,
  type ReindexResponse,
  type ReindexRequest,
  type IndexStalenessResponse,
} from "@/types";
import { isMockMode } from "@/lib/mock-data";

// =============================================================================
// MOCK DATA
// =============================================================================

const mockSearchResults: KBSearchResult[] = [
  {
    content: "The TaskService handles all task lifecycle operations including creation, assignment, status transitions, and completion tracking...",
    source: "roboco/services/tasks.py",
    score: 0.92,
    index_type: KBIndexType.CODE,
    metadata: { language: "python", lines: "45-120" },
  },
  {
    content: "## Task Lifecycle\n\nTasks follow a strict state machine from PENDING through IN_PROGRESS to COMPLETED. Each transition requires specific conditions...",
    source: "docs/architecture/task-lifecycle.md",
    score: 0.88,
    index_type: KBIndexType.DOCUMENTATION,
    metadata: { section: "Architecture" },
  },
  {
    content: "BE-DEV-1: I've completed the task validation logic. The acceptance criteria now require at least one item before a task can be created.",
    source: "channel:backend/session:abc123",
    score: 0.75,
    index_type: KBIndexType.CONVERSATIONS,
    metadata: { agent: "be-dev-1", timestamp: "2024-01-15T10:30:00Z" },
  },
  {
    content: "Learned that proper error boundaries in the task form prevent cascading failures. Applied this pattern to all form components.",
    source: "journal:be-dev-1/entry:xyz789",
    score: 0.71,
    index_type: KBIndexType.JOURNALS,
    metadata: { agent: "be-dev-1", type: "learning" },
  },
];

const mockStats: KBStats = {
  indexes: [
    { index_type: KBIndexType.CODE, document_count: 1250, chunk_count: 8500, last_updated: "2024-01-15T12:00:00Z" },
    { index_type: KBIndexType.DOCUMENTATION, document_count: 45, chunk_count: 320, last_updated: "2024-01-15T11:30:00Z" },
    { index_type: KBIndexType.CONVERSATIONS, document_count: 890, chunk_count: 4200, last_updated: "2024-01-15T12:15:00Z" },
    { index_type: KBIndexType.JOURNALS, document_count: 156, chunk_count: 780, last_updated: "2024-01-15T10:00:00Z" },
    { index_type: KBIndexType.ERRORS, document_count: 45, chunk_count: 180, last_updated: "2024-01-15T10:00:00Z" },
    { index_type: KBIndexType.STANDARDS, document_count: 12, chunk_count: 60, last_updated: "2024-01-15T10:00:00Z" },
    { index_type: KBIndexType.DECISIONS, document_count: 78, chunk_count: 390, last_updated: "2024-01-15T10:00:00Z" },
    { index_type: KBIndexType.REVIEWS, document_count: 234, chunk_count: 1170, last_updated: "2024-01-15T10:00:00Z" },
    { index_type: KBIndexType.LEARNINGS, document_count: 89, chunk_count: 445, last_updated: "2024-01-15T10:00:00Z" },
  ],
  total_documents: 2799,
  total_chunks: 15245,
};

// Backend returns stats as dict, we need to transform to array
interface BackendIndexStats {
  initialized: boolean;
  indexes: Record<string, { document_count: number; chunk_count: number; last_updated: string | null }>;
}

function transformStatsResponse(backendStats: BackendIndexStats): KBStats {
  const indexes: KBIndexStats[] = Object.entries(backendStats.indexes || {}).map(([indexType, stats]) => ({
    index_type: indexType as KBIndexType,
    document_count: stats.document_count ?? 0,
    chunk_count: stats.chunk_count ?? 0,
    last_updated: stats.last_updated ?? null,
  }));

  const total_documents = indexes.reduce((sum, idx) => sum + idx.document_count, 0);
  const total_chunks = indexes.reduce((sum, idx) => sum + idx.chunk_count, 0);

  return { indexes, total_documents, total_chunks };
}

// =============================================================================
// SEARCH ENDPOINTS
// =============================================================================

/**
 * Semantic search across indexed content
 */
async function search(params: KBSearchRequest): Promise<KBSearchResponse> {
  if (isMockMode()) {
    // Filter by index types if specified
    let results = [...mockSearchResults];
    if (params.index_types && params.index_types.length > 0) {
      results = results.filter((r) => params.index_types!.includes(r.index_type));
    }
    // Filter by min score
    if (params.min_score) {
      results = results.filter((r) => r.score >= params.min_score!);
    }
    // Limit results
    const topK = params.top_k ?? 10;
    results = results.slice(0, topK);

    return {
      results,
      total: results.length,
      query: params.query,
    };
  }

  const response = await api.post<KBSearchResponse>("/optimal/kb/search", params);
  return response.data;
}

// =============================================================================
// RAG ENDPOINTS
// =============================================================================

/**
 * RAG query - ask a question, get AI answer with citations
 */
async function ragQuery(params: RAGQueryRequest): Promise<RAGQueryResponse> {
  if (isMockMode()) {
    // Simulate RAG response with mock data
    return {
      answer: `Based on the indexed knowledge, here's what I found about "${params.question}":\n\nThe system uses a structured approach where tasks follow a defined lifecycle. Each task starts in PENDING state and can transition through various states like IN_PROGRESS, BLOCKED, and eventually COMPLETED or CANCELLED.\n\nKey points:\n- Tasks require acceptance criteria before creation\n- State transitions are validated by the TaskService\n- Agents can claim and work on tasks based on their role and team`,
      citations: mockSearchResults.slice(0, 3).map((r) => ({
        content: r.content,
        source: r.source,
        score: r.score,
        index_type: r.index_type,
        metadata: r.metadata,
      })),
      query: params.question,
      context_used: 3,
    };
  }

  // Transform frontend params to backend format
  const backendParams = {
    query: params.question,
    index_types: params.index_types,
    top_k: params.max_context_chunks ?? 5,
  };

  const response = await api.post<RAGQueryResponse>("/optimal/rag/query", backendParams);
  return response.data;
}

/**
 * Get context for a query without generating an answer
 */
async function getContext(params: RAGQueryRequest): Promise<KBSearchResult[]> {
  if (isMockMode()) {
    return mockSearchResults.slice(0, params.max_context_chunks ?? 5);
  }

  // Transform frontend params to backend format
  const backendParams = {
    query: params.question,
    index_types: params.index_types,
    top_k: params.max_context_chunks ?? 5,
  };

  const response = await api.post<{ context: KBSearchResult[] }>("/optimal/rag/context", backendParams);
  return response.data.context;
}

// =============================================================================
// STATS ENDPOINTS
// =============================================================================

/**
 * Get index statistics
 */
async function getStats(): Promise<KBStats> {
  if (isMockMode()) {
    return mockStats;
  }

  const response = await api.get<BackendIndexStats>("/optimal/stats");
  return transformStatsResponse(response.data);
}

/**
 * Get stats for a specific index type
 */
async function getIndexStats(indexType: KBIndexType): Promise<KBIndexStats> {
  if (isMockMode()) {
    const stats = mockStats.indexes.find((i) => i.index_type === indexType);
    if (stats) return stats;
    return {
      index_type: indexType,
      document_count: 0,
      chunk_count: 0,
      last_updated: null,
    };
  }

  const response = await api.get<KBIndexStats>(`/optimal/stats/${indexType}`);
  return response.data;
}

// =============================================================================
// BROWSE ENDPOINTS
// =============================================================================

/**
 * List documents in a specific index (for browsing)
 */
async function listDocuments(
  indexType: KBIndexType,
  params?: { limit?: number; offset?: number }
): Promise<{ documents: Array<{ id: string; source: string; indexed_at: string; metadata?: Record<string, unknown> }>; total: number }> {
  if (isMockMode()) {
    // Generate mock document list
    const docs = mockSearchResults
      .filter((r) => r.index_type === indexType)
      .map((r, idx) => ({
        id: `doc-${indexType}-${idx}`,
        source: r.source,
        indexed_at: "2024-01-15T12:00:00Z",
      }));
    return { documents: docs, total: docs.length };
  }

  const response = await api.get<{ documents: Array<{ id: string; source: string; indexed_at: string; metadata?: Record<string, unknown> }>; total: number; index_type: string }>(
    `/optimal/kb/${indexType}/documents`,
    { params }
  );
  return { documents: response.data.documents, total: response.data.total };
}

// =============================================================================
// HEALTH ENDPOINT
// =============================================================================

/**
 * Get RAG system health status
 */
async function getHealth(): Promise<RAGHealthResponse> {
  if (isMockMode()) {
    return {
      healthy: true,
      embedding_status: "healthy",
      llm_status: "healthy",
      vector_store_status: "healthy",
      details: {},
    };
  }
  const response = await api.get<RAGHealthResponse>("/optimal/health");
  return response.data;
}

// =============================================================================
// INDEX MANAGEMENT
// =============================================================================

/**
 * Delete/clear an index
 */
async function deleteIndex(indexType: KBIndexType): Promise<ClearIndexResponse> {
  if (isMockMode()) {
    return { status: "cleared", index_type: indexType };
  }
  const response = await api.delete<ClearIndexResponse>(`/optimal/kb/${indexType}`);
  return response.data;
}

/**
 * Refresh an index with updated sources
 */
async function refreshIndex(request: RefreshIndexRequest): Promise<RefreshIndexResponse> {
  if (isMockMode()) {
    return { status: "refreshed", index_type: request.index_type, sources: request.sources };
  }
  const response = await api.post<RefreshIndexResponse>("/optimal/kb/refresh", request);
  return response.data;
}

/**
 * Trigger full reindex with detailed reporting
 *
 * @param request - Optional parameters for reindexing
 * @param request.force - Force reindex even if indexes have content
 * @param request.timeout_seconds - Max time to wait (default: 300s)
 * @returns Detailed report of what was indexed, failed, and skipped
 */
async function reindexAll(request?: ReindexRequest): Promise<ReindexResponse> {
  if (isMockMode()) {
    return {
      status: "reindexed",
      code: {
        index_type: "code",
        total_attempted: 100,
        successful: 98,
        failed: 2,
        skipped: 0,
        success_rate: 98.0,
        has_failures: true,
        failed_sources: [
          ["/path/to/file1.py", "UTF-8 decode error"],
          ["/path/to/file2.py", "File too large"],
        ],
        duration_seconds: 45.2,
      },
      documentation: {
        index_type: "documentation",
        total_attempted: 50,
        successful: 50,
        failed: 0,
        skipped: 0,
        success_rate: 100.0,
        has_failures: false,
        failed_sources: [],
        duration_seconds: 12.5,
      },
      overall_success: true,
      warnings: ["Code indexing had 2 failures"],
      // Legacy fields
      code_count: 98,
      docs_count: 50,
    };
  }
  const response = await api.post<ReindexResponse>("/optimal/kb/reindex", null, {
    params: {
      force: request?.force ?? false,
      timeout_seconds: request?.timeout_seconds ?? 300,
    },
  });
  return response.data;
}

/**
 * Check if indexes are stale (source files modified after last indexing)
 *
 * @returns Staleness info for each index type
 */
async function checkStaleness(): Promise<IndexStalenessResponse> {
  if (isMockMode()) {
    return {
      needs_reindex: true,
      stale_indexes: ["code"],
      details: {
        code: {
          status: "stale",
          last_indexed: new Date(Date.now() - 86400000).toISOString(),
          stale_file_count: 5,
          stale_files_sample: [
            "/roboco/services/task.py",
            "/roboco/services/optimal.py",
          ],
          recommendation: "Run /kb/reindex?force=true to update",
        },
        documentation: {
          status: "current",
          last_indexed: new Date().toISOString(),
          indexed_sources_count: 50,
        },
      },
    };
  }
  const response = await api.get<IndexStalenessResponse>(
    "/optimal/stats/staleness"
  );
  return response.data;
}

// =============================================================================
// MENTOR ENDPOINTS
// =============================================================================

/**
 * Ask the mentor for help
 */
async function askMentor(request: MentorAskRequest): Promise<MentorAskResponse> {
  if (isMockMode()) {
    return {
      answer: `Here's what I found about "${request.question}":\n\nBased on our organizational knowledge, I recommend following the established patterns and consulting the relevant documentation.`,
      sources: mockSearchResults.slice(0, 2),
      conversation_id: "conv-" + Date.now(),
      suggested_followups: ["Can you elaborate?", "What are the alternatives?"],
    };
  }
  const response = await api.post<MentorAskResponse>("/optimal/mentor/ask", request);
  return response.data;
}

// =============================================================================
// ERROR ENDPOINTS
// =============================================================================

/**
 * Search for known error solutions
 */
async function searchErrors(request: ErrorSearchRequest): Promise<ErrorSearchResponse> {
  if (isMockMode()) {
    return { results: [], total: 0 };
  }
  const response = await api.post<ErrorSearchResponse>("/optimal/errors/search", request);
  return response.data;
}

/**
 * Record an error solution
 */
async function recordError(request: ErrorRecordRequest): Promise<ErrorRecordResponse> {
  if (isMockMode()) {
    return { error_id: "err-" + Date.now(), status: "recorded" };
  }
  const response = await api.post<ErrorRecordResponse>("/optimal/errors/record", request);
  return response.data;
}

// =============================================================================
// DECISION ENDPOINTS
// =============================================================================

/**
 * Check if a similar decision was made before
 */
async function checkDecision(request: DecisionCheckRequest): Promise<DecisionCheckResponse> {
  if (isMockMode()) {
    return { has_precedent: false, decisions: [], recommendation: "No similar decisions found" };
  }
  const response = await api.post<DecisionCheckResponse>("/optimal/decisions/check", request);
  return response.data;
}

/**
 * Record a decision for future reference
 */
async function recordDecision(request: DecisionRecordRequest): Promise<DecisionRecordResponse> {
  if (isMockMode()) {
    return { decision_id: "dec-" + Date.now(), status: "recorded" };
  }
  const response = await api.post<DecisionRecordResponse>("/optimal/decisions/record", request);
  return response.data;
}

// =============================================================================
// STANDARDS ENDPOINTS
// =============================================================================

/**
 * Get standards for a domain
 */
async function getStandards(request: StandardsGetRequest): Promise<StandardsGetResponse> {
  if (isMockMode()) {
    return { standards: [], total: 0 };
  }
  const response = await api.post<StandardsGetResponse>("/optimal/standards/get", request);
  return response.data;
}

/**
 * Validate an action against standards
 */
async function validateAction(request: ValidateActionRequest): Promise<ValidateActionResponse> {
  if (isMockMode()) {
    return { allowed: true, violations: [], warnings: [], relevant_standards: [] };
  }
  const response = await api.post<ValidateActionResponse>("/optimal/standards/validate", request);
  return response.data;
}

// =============================================================================
// CODE REVIEW ENDPOINTS
// =============================================================================

/**
 * Review code against standards
 */
async function reviewCode(request: CodeReviewRequest): Promise<CodeReviewResponse> {
  if (isMockMode()) {
    return {
      file_path: request.file_path,
      approved: true,
      score: 85,
      comments: [],
      standards_checked: ["coding-standards"],
      similar_reviews: [],
    };
  }
  const response = await api.post<CodeReviewResponse>("/optimal/review/code", request);
  return response.data;
}

// =============================================================================
// LEARNING ENDPOINTS
// =============================================================================

/**
 * Record a learning for knowledge sharing
 */
async function recordLearning(request: LearningRecordRequest): Promise<LearningRecordResponse> {
  if (isMockMode()) {
    return { learning_id: "learn-" + Date.now(), status: "recorded" };
  }
  const response = await api.post<LearningRecordResponse>("/optimal/learnings/record", request);
  return response.data;
}

/**
 * Search for relevant learnings
 */
async function searchLearnings(request: LearningSearchRequest): Promise<KBSearchResponse> {
  if (isMockMode()) {
    return { results: [], total: 0, query: request.query };
  }
  const response = await api.post<KBSearchResponse>("/optimal/learnings/search", request);
  return response.data;
}

// =============================================================================
// PROACTIVE CONTEXT ENDPOINTS
// =============================================================================

/**
 * Get proactive context for a task
 */
async function getProactiveContext(request: ProactiveContextRequest): Promise<ProactiveContextResponse> {
  if (isMockMode()) {
    return {
      task_id: request.task_id,
      similar_tasks: [],
      relevant_learnings: [],
      code_patterns: [],
      applicable_standards: [],
      recent_decisions: [],
      known_issues: [],
      summary: "No relevant context found for this task.",
    };
  }
  const response = await api.post<ProactiveContextResponse>("/optimal/context/proactive", request);
  return response.data;
}

// =============================================================================
// TOKEN ESTIMATION
// =============================================================================

/**
 * Estimate token count for content
 */
async function estimateTokens(request: TokenEstimateRequest): Promise<TokenEstimateResponse> {
  if (isMockMode()) {
    return {
      token_count: Math.ceil(request.content.length / 4),
      model: request.model || "claude-sonnet-4-20250514",
      content_length: request.content.length,
    };
  }
  const response = await api.post<TokenEstimateResponse>("/optimal/tokens/estimate", request);
  return response.data;
}

// =============================================================================
// EXPORT API
// =============================================================================

export const knowledgeBaseApi = {
  // Search
  search,

  // RAG
  ragQuery,
  getContext,

  // Stats & Health
  getStats,
  getIndexStats,
  getHealth,

  // Browse
  listDocuments,

  // Index Management
  deleteIndex,
  refreshIndex,
  reindexAll,
  checkStaleness,

  // Mentor
  askMentor,

  // Errors
  searchErrors,
  recordError,

  // Decisions
  checkDecision,
  recordDecision,

  // Standards
  getStandards,
  validateAction,

  // Code Review
  reviewCode,

  // Learnings
  recordLearning,
  searchLearnings,

  // Proactive Context
  getProactiveContext,

  // Token Estimation
  estimateTokens,
};

export default knowledgeBaseApi;
