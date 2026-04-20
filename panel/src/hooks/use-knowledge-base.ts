"use client";

/**
 * Knowledge Base Hooks
 *
 * React Query hooks for KB search and RAG operations.
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { knowledgeBaseApi } from "@/lib/api/knowledge-base";
import type {
  KBSearchRequest,
  KBSearchResponse,
  RAGQueryRequest,
  RAGQueryResponse,
  KBStats,
  KBIndexType,
  KBIndexStats,
  RAGHealthResponse,
  MentorAskRequest,
  MentorAskResponse,
  ErrorSearchRequest,
  ErrorSearchResponse,
  ErrorRecordRequest,
  ErrorRecordResponse,
  DecisionCheckRequest,
  DecisionCheckResponse,
  DecisionRecordRequest,
  DecisionRecordResponse,
  StandardsGetRequest,
  StandardsGetResponse,
  ValidateActionRequest,
  ValidateActionResponse,
  CodeReviewRequest,
  CodeReviewResponse,
  LearningRecordRequest,
  LearningRecordResponse,
  LearningSearchRequest,
  ProactiveContextResponse,
  TokenEstimateRequest,
  TokenEstimateResponse,
  RefreshIndexRequest,
  RefreshIndexResponse,
  ClearIndexResponse,
  ReindexResponse,
  ReindexRequest,
  IndexStalenessResponse,
} from "@/types";

// =============================================================================
// QUERY KEYS
// =============================================================================

export const kbKeys = {
  all: ["knowledge-base"] as const,
  stats: () => [...kbKeys.all, "stats"] as const,
  indexStats: (indexType: KBIndexType) => [...kbKeys.all, "stats", indexType] as const,
  health: () => [...kbKeys.all, "health"] as const,
  staleness: () => [...kbKeys.all, "staleness"] as const,
  search: (query: string, filters?: string) => [...kbKeys.all, "search", query, filters] as const,
  documents: (indexType: KBIndexType, params?: string) => [...kbKeys.all, "documents", indexType, params] as const,
  learnings: (query: string, filters?: string) => [...kbKeys.all, "learnings", query, filters] as const,
  proactiveContext: (taskId: string) => [...kbKeys.all, "proactive-context", taskId] as const,
};

// =============================================================================
// STATS QUERIES
// =============================================================================

/**
 * Get KB index statistics
 */
export function useKBStats() {
  return useQuery<KBStats>({
    queryKey: kbKeys.stats(),
    queryFn: () => knowledgeBaseApi.getStats(),
    staleTime: 1000 * 60 * 5, // 5 minutes
    retry: 1,
  });
}

/**
 * Get stats for a specific index type
 */
export function useKBIndexStats(indexType: KBIndexType) {
  return useQuery<KBIndexStats>({
    queryKey: kbKeys.indexStats(indexType),
    queryFn: () => knowledgeBaseApi.getIndexStats(indexType),
    staleTime: 1000 * 60 * 5, // 5 minutes
  });
}

// =============================================================================
// SEARCH QUERIES
// =============================================================================

/**
 * Semantic search (enabled only when query has content)
 */
export function useKBSearch(params: KBSearchRequest, enabled = true) {
  return useQuery<KBSearchResponse>({
    queryKey: kbKeys.search(params.query, JSON.stringify(params.index_types)),
    queryFn: () => knowledgeBaseApi.search(params),
    enabled: enabled && params.query.length >= 3,
    staleTime: 1000 * 60 * 2, // 2 minutes
  });
}

// =============================================================================
// BROWSE QUERIES
// =============================================================================

/**
 * List documents in a specific index
 */
export function useKBDocuments(
  indexType: KBIndexType,
  params?: { limit?: number; offset?: number },
  enabled = true
) {
  return useQuery({
    queryKey: kbKeys.documents(indexType, JSON.stringify(params)),
    queryFn: () => knowledgeBaseApi.listDocuments(indexType, params),
    enabled,
    staleTime: 1000 * 60 * 5, // 5 minutes
  });
}

// =============================================================================
// MUTATIONS
// =============================================================================

/**
 * RAG Query - mutation because it triggers AI generation
 */
export function useRAGQuery() {
  return useMutation<RAGQueryResponse, Error, RAGQueryRequest>({
    mutationFn: (params) => knowledgeBaseApi.ragQuery(params),
  });
}

/**
 * Get context without generating answer
 */
export function useRAGContext() {
  return useMutation({
    mutationFn: (params: RAGQueryRequest) => knowledgeBaseApi.getContext(params),
  });
}

// =============================================================================
// HEALTH QUERY
// =============================================================================

/**
 * Get RAG system health status
 */
export function useRAGHealth() {
  return useQuery<RAGHealthResponse>({
    queryKey: kbKeys.health(),
    queryFn: () => knowledgeBaseApi.getHealth(),
    staleTime: 1000 * 60, // 1 minute
    refetchInterval: 1000 * 60 * 5, // Check every 5 minutes
  });
}

// =============================================================================
// INDEX MANAGEMENT MUTATIONS
// =============================================================================

/**
 * Delete/clear an index
 */
export function useDeleteIndex() {
  const queryClient = useQueryClient();

  return useMutation<ClearIndexResponse, Error, KBIndexType>({
    mutationFn: (indexType) => knowledgeBaseApi.deleteIndex(indexType),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: kbKeys.stats() });
    },
  });
}

/**
 * Refresh an index
 */
export function useRefreshIndex() {
  const queryClient = useQueryClient();

  return useMutation<RefreshIndexResponse, Error, RefreshIndexRequest>({
    mutationFn: (request) => knowledgeBaseApi.refreshIndex(request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: kbKeys.stats() });
    },
  });
}

/**
 * Reindex all with detailed reporting
 *
 * Returns detailed IndexingReport for both code and documentation,
 * including success/failure counts and failed file paths.
 */
export function useReindexAll() {
  const queryClient = useQueryClient();

  return useMutation<ReindexResponse, Error, ReindexRequest | undefined>({
    mutationFn: (request) => knowledgeBaseApi.reindexAll(request),
    onSuccess: () => {
      // Invalidate all relevant queries after reindexing
      queryClient.invalidateQueries({ queryKey: kbKeys.stats() });
      queryClient.invalidateQueries({ queryKey: kbKeys.staleness() });
      queryClient.invalidateQueries({ queryKey: kbKeys.health() });
    },
  });
}

/**
 * Check if indexes are stale (source files modified after indexing)
 *
 * Use this to show a "Reindex recommended" warning in the UI.
 */
export function useIndexStaleness() {
  return useQuery<IndexStalenessResponse>({
    queryKey: kbKeys.staleness(),
    queryFn: () => knowledgeBaseApi.checkStaleness(),
    staleTime: 5 * 60 * 1000, // 5 minutes
    refetchInterval: 10 * 60 * 1000, // Check every 10 minutes
  });
}

// =============================================================================
// MENTOR MUTATIONS
// =============================================================================

/**
 * Ask the mentor for help
 */
export function useAskMentor() {
  return useMutation<MentorAskResponse, Error, MentorAskRequest>({
    mutationFn: (request) => knowledgeBaseApi.askMentor(request),
  });
}

// =============================================================================
// ERROR MUTATIONS
// =============================================================================

/**
 * Search for known error solutions
 */
export function useSearchErrors() {
  return useMutation<ErrorSearchResponse, Error, ErrorSearchRequest>({
    mutationFn: (request) => knowledgeBaseApi.searchErrors(request),
  });
}

/**
 * Record an error solution
 */
export function useRecordError() {
  return useMutation<ErrorRecordResponse, Error, ErrorRecordRequest>({
    mutationFn: (request) => knowledgeBaseApi.recordError(request),
  });
}

// =============================================================================
// DECISION MUTATIONS
// =============================================================================

/**
 * Check if a similar decision was made before
 */
export function useCheckDecision() {
  return useMutation<DecisionCheckResponse, Error, DecisionCheckRequest>({
    mutationFn: (request) => knowledgeBaseApi.checkDecision(request),
  });
}

/**
 * Record a decision for future reference
 */
export function useRecordDecision() {
  return useMutation<DecisionRecordResponse, Error, DecisionRecordRequest>({
    mutationFn: (request) => knowledgeBaseApi.recordDecision(request),
  });
}

// =============================================================================
// STANDARDS MUTATIONS
// =============================================================================

/**
 * Get standards for a domain
 */
export function useGetStandards() {
  return useMutation<StandardsGetResponse, Error, StandardsGetRequest>({
    mutationFn: (request) => knowledgeBaseApi.getStandards(request),
  });
}

/**
 * Validate an action against standards
 */
export function useValidateAction() {
  return useMutation<ValidateActionResponse, Error, ValidateActionRequest>({
    mutationFn: (request) => knowledgeBaseApi.validateAction(request),
  });
}

// =============================================================================
// CODE REVIEW MUTATIONS
// =============================================================================

/**
 * Review code against standards
 */
export function useReviewCode() {
  return useMutation<CodeReviewResponse, Error, CodeReviewRequest>({
    mutationFn: (request) => knowledgeBaseApi.reviewCode(request),
  });
}

// =============================================================================
// LEARNING HOOKS
// =============================================================================

/**
 * Record a learning
 */
export function useRecordLearning() {
  return useMutation<LearningRecordResponse, Error, LearningRecordRequest>({
    mutationFn: (request) => knowledgeBaseApi.recordLearning(request),
  });
}

/**
 * Search learnings (as query for persistent results)
 */
export function useSearchLearnings(request: LearningSearchRequest, enabled = true) {
  return useQuery<KBSearchResponse>({
    queryKey: kbKeys.learnings(request.query, JSON.stringify({ category: request.category, team: request.team })),
    queryFn: () => knowledgeBaseApi.searchLearnings(request),
    enabled: enabled && request.query.length >= 3,
    staleTime: 1000 * 60 * 2, // 2 minutes
  });
}

// =============================================================================
// PROACTIVE CONTEXT HOOKS
// =============================================================================

/**
 * Get proactive context for a task
 */
export function useProactiveContext(taskId: string, enabled = true) {
  return useQuery<ProactiveContextResponse>({
    queryKey: kbKeys.proactiveContext(taskId),
    queryFn: () => knowledgeBaseApi.getProactiveContext({ task_id: taskId }),
    enabled: enabled && !!taskId,
    staleTime: 1000 * 60 * 5, // 5 minutes
  });
}

// =============================================================================
// TOKEN ESTIMATION
// =============================================================================

/**
 * Estimate token count
 */
export function useEstimateTokens() {
  return useMutation<TokenEstimateResponse, Error, TokenEstimateRequest>({
    mutationFn: (request) => knowledgeBaseApi.estimateTokens(request),
  });
}
