import api from "./client";
import type { Team, TaskType, TaskNature, Complexity } from "@/types";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** One cell's slice of the work — the per-cell breakdown of The Work. */
export interface CellWork {
  team: Team;
  summary: string;
  items: string[];
}

/** A structured GOLD task draft, mirroring the backend PrompterDraftTask. */
export interface DraftProposal {
  title: string;
  description: string;
  acceptance_criteria: string[];
  team: Team;
  priority?: number;
  task_type?: TaskType;
  nature?: TaskNature;
  estimated_complexity?: Complexity;
  // Structured GOLD fields
  objective?: string | null;
  what_this_builds?: string[];
  the_work?: CellWork[];
  notes?: string[];
  // Targeting (resolved at confirm time)
  project_id?: string | null;
  product_id?: string | null;
}

export type DraftScale = "single" | "multi";

export interface ChatResponse {
  reply: string;
  draft?: DraftProposal | null;
  draftReady: boolean;
  scale: DraftScale | null;
  session_id: string;
}

export interface CreateSessionResponse {
  session_id: string;
}

/** What the human picked/edited at confirm time. */
export interface ConfirmPayload {
  project_id?: string;
  product_id?: string;
  draft?: DraftProposal;
}

// A message record as returned by the backend (PrompterMessageResponse).
interface BackendMessage {
  id: string;
  session_id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

// The turn envelope returned by POST /sessions/{id}/messages
// (PrompterTurnResponse). The backend now owns the draft-ready judgement, so
// the frontend no longer re-derives it by matching phrases.
interface TurnResponse {
  messages: BackendMessage[];
  draft_ready: boolean;
  scale: DraftScale | null;
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

export const prompterApi = {
  /**
   * Create a new prompter session, returning a session ID. The endpoint
   * requires a JSON body (optional `context`), so send `{}`, and map the
   * backend's `id` field onto our `session_id`.
   */
  createSession: async (): Promise<CreateSessionResponse> => {
    const { data } = await api.post<{ id: string }>("/prompter/sessions", {});
    return { session_id: data.id };
  },

  /**
   * Send a chat message in an existing session. The backend appends the user
   * message, replies, and returns the turn envelope. We surface the latest
   * assistant message as the reply and, when the backend signals readiness,
   * fetch the structured draft.
   */
  sendMessage: async (
    sessionId: string,
    message: string
  ): Promise<ChatResponse> => {
    const { data } = await api.post<TurnResponse>(
      `/prompter/sessions/${sessionId}/messages`,
      { content: message }
    );
    const lastAssistant = [...data.messages]
      .reverse()
      .find((m) => m.role === "assistant");
    const reply = lastAssistant?.content ?? "";

    let draft: DraftProposal | null = null;
    if (data.draft_ready) {
      try {
        draft = await prompterApi.getDraft(sessionId);
      } catch {
        draft = null;
      }
    }
    return {
      reply,
      draft,
      draftReady: data.draft_ready,
      scale: data.scale,
      session_id: sessionId,
    };
  },

  /**
   * Fetch the current structured draft for a session (if one exists).
   * The backend returns a TaskDraftResponse whose `draft` field holds the task.
   */
  getDraft: async (sessionId: string): Promise<DraftProposal | null> => {
    const { data } = await api.get<{ draft: DraftProposal | null }>(
      `/prompter/sessions/${sessionId}/draft`
    );
    return data.draft;
  },

  /**
   * Confirm the draft → create the real task through the Prompter's own
   * confirm endpoint (which routes single-cell vs board-led multi-cell and
   * sets confirmed_by_human). The human's project/product choice and any
   * edits to the structured draft travel in the payload.
   */
  confirm: async (
    sessionId: string,
    payload: ConfirmPayload
  ): Promise<{ task_id: string }> => {
    const { data } = await api.post<{ task_id: string }>(
      `/prompter/sessions/${sessionId}/confirm`,
      payload
    );
    return data;
  },
};
