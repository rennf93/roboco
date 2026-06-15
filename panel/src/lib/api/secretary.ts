import api, { API_URL } from "./client";

// ---------------------------------------------------------------------------
// Secretary — the CEO's chief-of-staff. Two surfaces:
//   1. a live chat (spawned Secretary container, SSE stream) the CEO talks to;
//   2. the directive queue — high-impact actions the Secretary proposed that
//      wait for the CEO's explicit confirmation (the gate list).
// The panel calls everything as the CEO (see client.ts interceptor).
// ---------------------------------------------------------------------------

export interface SecretaryDirective {
  id: string;
  kind: string;
  status: string;
  payload: Record<string, unknown>;
  requested_by: string;
  requested_at?: string | null;
  decided_by?: string | null;
  decided_at?: string | null;
  result?: string | null;
}

export interface CompanyState {
  goals: Record<string, unknown>;
  task_counts: Record<string, number>;
  pending_pitches: Record<string, unknown>[];
  pending_directives: SecretaryDirective[];
}

export type LiveEventKind =
  | "text"
  | "thinking"
  | "tool_use"
  | "tool_result"
  | "turn_end"
  | "system"
  | "error";

export interface LiveEvent {
  kind: LiveEventKind;
  text?: string;
  tool?: string;
  data?: Record<string, unknown>;
}

export const LIVE_EVENT_KINDS: LiveEventKind[] = [
  "text",
  "thinking",
  "tool_use",
  "tool_result",
  "turn_end",
  "system",
  "error",
];

export const secretaryApi = {
  /** Spawn the Secretary agent for a new chat. */
  startLive: async (initialMessage?: string): Promise<{ session_id: string }> => {
    const { data } = await api.post<{ session_id: string }>(
      "/secretary/live/start",
      { initial_message: initialMessage }
    );
    return data;
  },

  /** SSE URL the panel opens to watch the Secretary reply. */
  streamUrl: (sessionId: string): string =>
    `${API_URL}/secretary/live/${sessionId}/stream`,

  /** Is this session still running? */
  status: async (sessionId: string): Promise<{ alive: boolean }> => {
    const { data } = await api.get<{ alive: boolean }>(
      `/secretary/live/${sessionId}/status`
    );
    return data;
  },

  /** Deliver the CEO's message to the running Secretary; the reply streams back. */
  sendMessage: async (sessionId: string, text: string): Promise<void> => {
    await api.post(`/secretary/live/${sessionId}/messages`, { text });
  },

  /** Reap the session. */
  stop: async (sessionId: string): Promise<void> => {
    await api.post(`/secretary/live/${sessionId}/stop`);
  },

  /** List directives (CEO only); defaults to the pending queue. */
  listDirectives: async (
    statusFilter = "pending"
  ): Promise<SecretaryDirective[]> => {
    const { data } = await api.get<SecretaryDirective[]>("/secretary/directives", {
      params: { status_filter: statusFilter },
    });
    return data;
  },

  /** Confirm a pending directive — it executes with CEO authority. */
  confirmDirective: async (id: string): Promise<SecretaryDirective> => {
    const { data } = await api.post<SecretaryDirective>(
      `/secretary/directives/${id}/confirm`
    );
    return data;
  },

  /** Reject a pending directive. */
  rejectDirective: async (
    id: string,
    reason?: string
  ): Promise<SecretaryDirective> => {
    const { data } = await api.post<SecretaryDirective>(
      `/secretary/directives/${id}/reject`,
      { reason }
    );
    return data;
  },

  /** A compact snapshot of company state. */
  state: async (): Promise<CompanyState> => {
    const { data } = await api.get<CompanyState>("/secretary/state");
    return data;
  },
};
