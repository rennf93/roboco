/**
 * Journals API Client
 *
 * Agent personal journals for reflection, growth tracking, and debugging.
 * Matches backend: roboco/api/routes/journals.py
 */

import api from "./client";
import {
  Journal,
  JournalEntry,
  JournalEntryCreate,
  JournalEntryType,
  JournalStats,
  GrowthMetrics,
} from "@/types";
import {
  isMockMode,
  mockJournals,
  mockJournalEntries,
} from "@/lib/mock-data";

// =============================================================================
// JOURNAL ENDPOINTS
// =============================================================================

/**
 * Get the current agent's journal (CEO's journal in this case)
 */
async function getMyJournal(): Promise<Journal> {
  if (isMockMode()) {
    return mockJournals[0] as Journal;
  }
  const response = await api.get<Journal>("/journals/me");
  return response.data;
}

/**
 * Get a journal by agent ID or slug (e.g., "be-dev-1")
 */
async function getJournalByAgent(agentIdOrSlug: string): Promise<Journal> {
  if (isMockMode()) {
    const journal = mockJournals.find((j) => j.agent_id === agentIdOrSlug);
    if (journal) return journal as Journal;
    // Return first journal as fallback
    return mockJournals[0] as Journal;
  }
  const response = await api.get<Journal>(`/journals/${agentIdOrSlug}`);
  return response.data;
}

/**
 * List entries for a specific agent by ID or slug
 */
async function listAgentEntries(
  agentIdOrSlug: string,
  params?: {
    entry_type?: JournalEntryType;
    task_id?: string;
    limit?: number;
    offset?: number;
  }
): Promise<JournalEntry[]> {
  if (isMockMode()) {
    let entries = [...mockJournalEntries] as JournalEntry[];
    if (params?.entry_type) {
      entries = entries.filter((e) => e.type === params.entry_type);
    }
    if (params?.task_id) {
      entries = entries.filter((e) => e.task_id === params.task_id);
    }
    const offset = params?.offset ?? 0;
    const limit = params?.limit ?? 50;
    return entries.slice(offset, offset + limit);
  }
  const response = await api.get<JournalEntry[]>(
    `/journals/${agentIdOrSlug}/entries`,
    { params }
  );
  return response.data;
}

// =============================================================================
// ENTRY CRUD ENDPOINTS
// =============================================================================

/**
 * Create a new journal entry
 */
async function createEntry(data: JournalEntryCreate): Promise<JournalEntry> {
  if (isMockMode()) {
    const newEntry: JournalEntry = {
      id: `entry-${Date.now()}`,
      journal_id: mockJournals[0].id,
      type: data.type,
      title: data.title,
      content: data.content,
      task_id: data.task_id ?? null,
      session_id: data.session_id ?? null,
      timestamp: new Date().toISOString(),
      tags: data.tags ?? [],
      sentiment: data.sentiment ?? null,
      is_private: data.is_private ?? false,
      created_at: new Date().toISOString(),
      updated_at: null,
    };
    (mockJournalEntries as JournalEntry[]).push(newEntry);
    return newEntry;
  }
  const response = await api.post<JournalEntry>("/journals/me/entries", data);
  return response.data;
}

/**
 * List current agent's journal entries
 */
async function listMyEntries(params?: {
  entry_type?: JournalEntryType;
  task_id?: string;
  limit?: number;
  offset?: number;
}): Promise<JournalEntry[]> {
  if (isMockMode()) {
    let entries = [...mockJournalEntries] as JournalEntry[];
    if (params?.entry_type) {
      entries = entries.filter((e) => e.type === params.entry_type);
    }
    if (params?.task_id) {
      entries = entries.filter((e) => e.task_id === params.task_id);
    }
    const offset = params?.offset ?? 0;
    const limit = params?.limit ?? 50;
    return entries.slice(offset, offset + limit);
  }
  const response = await api.get<JournalEntry[]>("/journals/me/entries", {
    params,
  });
  return response.data;
}

/**
 * Get a specific journal entry by ID
 */
async function getEntry(entryId: string): Promise<JournalEntry> {
  if (isMockMode()) {
    const entry = mockJournalEntries.find((e) => e.id === entryId);
    if (entry) return entry as JournalEntry;
    throw new Error("Entry not found");
  }
  const response = await api.get<JournalEntry>(`/journals/entries/${entryId}`);
  return response.data;
}

/**
 * Delete a journal entry
 */
async function deleteEntry(entryId: string): Promise<void> {
  if (isMockMode()) {
    const idx = mockJournalEntries.findIndex((e) => e.id === entryId);
    if (idx !== -1) mockJournalEntries.splice(idx, 1);
    return;
  }
  await api.delete(`/journals/entries/${entryId}`);
}

// =============================================================================
// CONVENIENCE ENTRY ENDPOINTS
// =============================================================================

/**
 * Add a task reflection entry
 */
async function addTaskReflection(data: {
  task_id: string;
  title: string;
  what_done: string;
  what_learned: string;
  what_struggled?: string;
  next_steps?: string[];
  tags?: string[];
}): Promise<JournalEntry> {
  if (isMockMode()) {
    return createEntry({
      type: JournalEntryType.TASK_REFLECTION,
      title: data.title,
      content: JSON.stringify({
        what_done: data.what_done,
        what_learned: data.what_learned,
        what_struggled: data.what_struggled,
        next_steps: data.next_steps,
      }),
      task_id: data.task_id,
      tags: data.tags,
    });
  }
  const response = await api.post<JournalEntry>(
    "/journals/me/reflections",
    data
  );
  return response.data;
}

/**
 * Add a decision log entry
 */
async function addDecisionLog(data: {
  title: string;
  context: string;
  options: string[];
  chosen: string;
  rationale: string;
  consequences?: string;
  task_id?: string;
  tags?: string[];
}): Promise<JournalEntry> {
  if (isMockMode()) {
    return createEntry({
      type: JournalEntryType.DECISION_LOG,
      title: data.title,
      content: JSON.stringify({
        context: data.context,
        options: data.options,
        chosen: data.chosen,
        rationale: data.rationale,
        consequences: data.consequences,
      }),
      task_id: data.task_id,
      tags: data.tags,
    });
  }
  const response = await api.post<JournalEntry>("/journals/me/decisions", data);
  return response.data;
}

/**
 * Add a learning entry
 */
async function addLearning(data: {
  title: string;
  what_learned: string;
  how_applied?: string;
  source?: string;
  task_id?: string;
  tags?: string[];
}): Promise<JournalEntry> {
  if (isMockMode()) {
    return createEntry({
      type: JournalEntryType.LEARNING,
      title: data.title,
      content: JSON.stringify({
        what_learned: data.what_learned,
        how_applied: data.how_applied,
        source: data.source,
      }),
      task_id: data.task_id,
      tags: data.tags,
    });
  }
  const response = await api.post<JournalEntry>("/journals/me/learnings", data);
  return response.data;
}

/**
 * Add a struggle entry
 */
async function addStruggle(data: {
  title: string;
  what_struggled: string;
  attempted_solutions: string[];
  resolution?: string;
  help_needed?: string;
  task_id?: string;
  tags?: string[];
}): Promise<JournalEntry> {
  if (isMockMode()) {
    return createEntry({
      type: JournalEntryType.STRUGGLE,
      title: data.title,
      content: JSON.stringify({
        what_struggled: data.what_struggled,
        attempted_solutions: data.attempted_solutions,
        resolution: data.resolution,
        help_needed: data.help_needed,
      }),
      task_id: data.task_id,
      tags: data.tags,
    });
  }
  const response = await api.post<JournalEntry>("/journals/me/struggles", data);
  return response.data;
}

/**
 * Add a general journal entry
 */
async function addNote(data: {
  title: string;
  content: string;
  task_id?: string;
  session_id?: string;
  tags?: string[];
  is_private?: boolean;
}): Promise<JournalEntry> {
  if (isMockMode()) {
    return createEntry({
      type: JournalEntryType.GENERAL,
      title: data.title,
      content: data.content,
      task_id: data.task_id,
      session_id: data.session_id,
      tags: data.tags,
      is_private: data.is_private,
    });
  }
  const response = await api.post<JournalEntry>("/journals/me/notes", data);
  return response.data;
}

// =============================================================================
// ANALYTICS ENDPOINTS
// =============================================================================

/**
 * Get statistics for the current agent's journal
 */
async function getMyStats(): Promise<JournalStats> {
  if (isMockMode()) {
    const entries = mockJournalEntries as JournalEntry[];
    return {
      total_entries: entries.length,
      entries_by_type: {
        task_reflection: entries.filter((e) => e.type === JournalEntryType.TASK_REFLECTION).length,
        decision_log: entries.filter((e) => e.type === JournalEntryType.DECISION_LOG).length,
        learning: entries.filter((e) => e.type === JournalEntryType.LEARNING).length,
        struggle: entries.filter((e) => e.type === JournalEntryType.STRUGGLE).length,
        general: entries.filter((e) => e.type === JournalEntryType.GENERAL).length,
      },
      last_entry_at: entries[0]?.created_at ?? null,
      has_summary: false,
    };
  }
  const response = await api.get<JournalStats>("/journals/me/stats");
  return response.data;
}

/**
 * Get growth metrics for the current agent
 */
async function getMyGrowthMetrics(): Promise<GrowthMetrics> {
  if (isMockMode()) {
    const entries = mockJournalEntries as JournalEntry[];
    return {
      total_reflections: entries.filter((e) => e.type === JournalEntryType.TASK_REFLECTION).length,
      total_learnings: entries.filter((e) => e.type === JournalEntryType.LEARNING).length,
      total_struggles: entries.filter((e) => e.type === JournalEntryType.STRUGGLE).length,
      total_decisions: entries.filter((e) => e.type === JournalEntryType.DECISION_LOG).length,
      struggle_resolution_rate: 0.7,
      learning_frequency: 3.5,
      sentiment_trend: "improving",
    };
  }
  const response = await api.get<GrowthMetrics>("/journals/me/growth");
  return response.data;
}

// =============================================================================
// SEARCH ENDPOINTS
// =============================================================================

/**
 * Semantic search through journal entries
 */
async function searchEntries(
  query: string,
  topK: number = 10
): Promise<JournalEntry[]> {
  if (isMockMode()) {
    // Simple text search for mock mode
    const queryLower = query.toLowerCase();
    return (mockJournalEntries as JournalEntry[])
      .filter(
        (e) =>
          e.title.toLowerCase().includes(queryLower) ||
          e.content.toLowerCase().includes(queryLower)
      )
      .slice(0, topK);
  }
  const response = await api.post<JournalEntry[]>("/journals/me/search", {
    query,
    top_k: topK,
  });
  return response.data;
}

// =============================================================================
// EXPORT API
// =============================================================================

export const journalsApi = {
  // Journal
  getMyJournal,
  getJournalByAgent,

  // Entry CRUD
  createEntry,
  listMyEntries,
  listAgentEntries,
  getEntry,
  deleteEntry,

  // Convenience entry creators
  addTaskReflection,
  addDecisionLog,
  addLearning,
  addStruggle,
  addNote,

  // Analytics
  getMyStats,
  getMyGrowthMetrics,

  // Search
  searchEntries,
};

export default journalsApi;
