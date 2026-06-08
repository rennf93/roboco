import api from "./client";
import type { Team, TaskType, Complexity } from "@/types";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface DraftProposal {
  title: string;
  description: string;
  acceptance_criteria: string[];
  team: Team;
  priority?: number;
  task_type?: TaskType;
  estimated_complexity?: Complexity;
}

export interface ChatResponse {
  reply: string;
  draft?: DraftProposal | null;
  session_id: string;
}

export interface CreateSessionResponse {
  session_id: string;
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

export const prompterApi = {
  /**
   * Create a new prompter session, returning a session ID.
   */
  createSession: async (): Promise<CreateSessionResponse> => {
    const { data } = await api.post<CreateSessionResponse>("/prompter/sessions");
    return data;
  },

  /**
   * Send a chat message in an existing session.
   * Returns the assistant reply and, if ready, a draft task proposal.
   */
  sendMessage: async (
    sessionId: string,
    message: string
  ): Promise<ChatResponse> => {
    const { data } = await api.post<ChatResponse>(
      `/prompter/sessions/${sessionId}/chat`,
      { message }
    );
    return data;
  },

  /**
   * Fetch the current draft for a session (if the LLM has produced one).
   */
  getDraft: async (sessionId: string): Promise<DraftProposal | null> => {
    const { data } = await api.get<{ draft: DraftProposal | null }>(
      `/prompter/sessions/${sessionId}/draft`
    );
    return data.draft;
  },
};
