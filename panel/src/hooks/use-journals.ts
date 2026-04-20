/**
 * Journals Hooks
 *
 * React Query hooks for journal operations.
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { journalsApi } from "@/lib/api/journals";
import type {
  Journal,
  JournalEntry,
  JournalEntryCreate,
  JournalEntryType,
  JournalStats,
  GrowthMetrics,
} from "@/types";

// =============================================================================
// QUERY KEYS
// =============================================================================

export const journalKeys = {
  all: ["journals"] as const,
  myJournal: () => [...journalKeys.all, "my"] as const,
  journal: (agentId: string) => [...journalKeys.all, agentId] as const,
  entries: () => [...journalKeys.all, "entries"] as const,
  myEntries: (params?: { entry_type?: JournalEntryType; task_id?: string }) =>
    [...journalKeys.entries(), "my", params] as const,
  entry: (entryId: string) => [...journalKeys.entries(), entryId] as const,
  stats: () => [...journalKeys.all, "stats"] as const,
  myStats: () => [...journalKeys.stats(), "my"] as const,
  growth: () => [...journalKeys.all, "growth"] as const,
  myGrowth: () => [...journalKeys.growth(), "my"] as const,
  search: (query: string) => [...journalKeys.all, "search", query] as const,
};

// =============================================================================
// JOURNAL QUERIES
// =============================================================================

/**
 * Get the current agent's journal
 */
export function useMyJournal() {
  return useQuery<Journal>({
    queryKey: journalKeys.myJournal(),
    queryFn: journalsApi.getMyJournal,
    staleTime: 1000 * 60 * 5, // 5 minutes
  });
}

/**
 * Get a journal by agent ID or slug (e.g., "be-dev-1")
 */
export function useJournalByAgent(agentIdOrSlug: string, enabled = true) {
  return useQuery<Journal>({
    queryKey: journalKeys.journal(agentIdOrSlug),
    queryFn: () => journalsApi.getJournalByAgent(agentIdOrSlug),
    enabled: enabled && !!agentIdOrSlug,
    staleTime: 1000 * 60 * 5, // 5 minutes
  });
}

/**
 * List entries for a specific agent by ID or slug
 */
export function useAgentJournalEntries(
  agentIdOrSlug: string,
  params?: {
    entry_type?: JournalEntryType;
    task_id?: string;
    limit?: number;
    offset?: number;
  }
) {
  return useQuery<JournalEntry[]>({
    queryKey: [...journalKeys.entries(), "agent", agentIdOrSlug, params],
    queryFn: () => journalsApi.listAgentEntries(agentIdOrSlug, params),
    enabled: !!agentIdOrSlug,
    staleTime: 1000 * 60 * 2, // 2 minutes
  });
}

// =============================================================================
// ENTRY QUERIES
// =============================================================================

/**
 * List current agent's journal entries
 */
export function useMyJournalEntries(params?: {
  entry_type?: JournalEntryType;
  task_id?: string;
  limit?: number;
  offset?: number;
}) {
  return useQuery<JournalEntry[]>({
    queryKey: journalKeys.myEntries(params),
    queryFn: () => journalsApi.listMyEntries(params),
    staleTime: 1000 * 60 * 2, // 2 minutes
  });
}

/**
 * Get a specific journal entry
 */
export function useJournalEntry(entryId: string, enabled = true) {
  return useQuery<JournalEntry>({
    queryKey: journalKeys.entry(entryId),
    queryFn: () => journalsApi.getEntry(entryId),
    enabled,
    staleTime: 1000 * 60 * 5, // 5 minutes
  });
}

// =============================================================================
// ANALYTICS QUERIES
// =============================================================================

/**
 * Get journal statistics
 */
export function useMyJournalStats() {
  return useQuery<JournalStats>({
    queryKey: journalKeys.myStats(),
    queryFn: journalsApi.getMyStats,
    staleTime: 1000 * 60 * 5, // 5 minutes
  });
}

/**
 * Get growth metrics
 */
export function useMyGrowthMetrics() {
  return useQuery<GrowthMetrics>({
    queryKey: journalKeys.myGrowth(),
    queryFn: journalsApi.getMyGrowthMetrics,
    staleTime: 1000 * 60 * 5, // 5 minutes
  });
}

// =============================================================================
// MUTATIONS
// =============================================================================

/**
 * Create a new journal entry
 */
export function useCreateJournalEntry() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: JournalEntryCreate) => journalsApi.createEntry(data),
    onSuccess: () => {
      // Invalidate entries and stats
      queryClient.invalidateQueries({ queryKey: journalKeys.entries() });
      queryClient.invalidateQueries({ queryKey: journalKeys.stats() });
      queryClient.invalidateQueries({ queryKey: journalKeys.myJournal() });
    },
  });
}

/**
 * Delete a journal entry
 */
export function useDeleteJournalEntry() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (entryId: string) => journalsApi.deleteEntry(entryId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: journalKeys.entries() });
      queryClient.invalidateQueries({ queryKey: journalKeys.stats() });
      queryClient.invalidateQueries({ queryKey: journalKeys.myJournal() });
    },
  });
}

/**
 * Add a task reflection
 */
export function useAddTaskReflection() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: Parameters<typeof journalsApi.addTaskReflection>[0]) =>
      journalsApi.addTaskReflection(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: journalKeys.entries() });
      queryClient.invalidateQueries({ queryKey: journalKeys.stats() });
      queryClient.invalidateQueries({ queryKey: journalKeys.growth() });
    },
  });
}

/**
 * Add a decision log
 */
export function useAddDecisionLog() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: Parameters<typeof journalsApi.addDecisionLog>[0]) =>
      journalsApi.addDecisionLog(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: journalKeys.entries() });
      queryClient.invalidateQueries({ queryKey: journalKeys.stats() });
      queryClient.invalidateQueries({ queryKey: journalKeys.growth() });
    },
  });
}

/**
 * Add a learning entry
 */
export function useAddLearning() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: Parameters<typeof journalsApi.addLearning>[0]) =>
      journalsApi.addLearning(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: journalKeys.entries() });
      queryClient.invalidateQueries({ queryKey: journalKeys.stats() });
      queryClient.invalidateQueries({ queryKey: journalKeys.growth() });
    },
  });
}

/**
 * Add a struggle entry
 */
export function useAddStruggle() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: Parameters<typeof journalsApi.addStruggle>[0]) =>
      journalsApi.addStruggle(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: journalKeys.entries() });
      queryClient.invalidateQueries({ queryKey: journalKeys.stats() });
      queryClient.invalidateQueries({ queryKey: journalKeys.growth() });
    },
  });
}

/**
 * Add a general note
 */
export function useAddNote() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: Parameters<typeof journalsApi.addNote>[0]) =>
      journalsApi.addNote(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: journalKeys.entries() });
      queryClient.invalidateQueries({ queryKey: journalKeys.stats() });
    },
  });
}

/**
 * Search journal entries
 */
export function useSearchJournalEntries(query: string, topK = 10) {
  return useQuery<JournalEntry[]>({
    queryKey: journalKeys.search(query),
    queryFn: () => journalsApi.searchEntries(query, topK),
    enabled: query.length > 2, // Only search if query is meaningful
    staleTime: 1000 * 60 * 2, // 2 minutes
  });
}
