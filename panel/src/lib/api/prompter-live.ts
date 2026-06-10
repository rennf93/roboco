import api, { API_URL } from "./client";
import type { ConfirmPayload } from "./prompter";

// ---------------------------------------------------------------------------
// Live intake chat — the panel side of the spawned-agent bridge.
//
// Unlike the legacy Ollama session API (`prompter.ts`), the brain here is a
// real spawned Claude Code agent. The panel:
//   1. POSTs /prompter/live/start with the scope (project XOR product) + the
//      opening message → gets a session id (the agent is spawned, the opening
//      message is delivered server-side once its container is reachable).
//   2. opens an SSE stream and watches the agent work (token deltas, tool
//      calls), and renders the draft card when the agent proposes one.
//   3. POSTs each subsequent message to /messages; replies arrive over SSE.
//   4. on confirm, /confirm turns the draft into a task and reaps the agent.
// ---------------------------------------------------------------------------

/** Open a live chat scoped to exactly one of project / product. */
export interface StartLivePayload {
  project_id?: string;
  product_id?: string;
  initial_message?: string;
}

export interface StartLiveResponse {
  session_id: string;
}

/** Event kinds the container relays — mirrors the backend driver.StreamChunk. */
export type LiveEventKind =
  | "text"
  | "thinking"
  | "tool_use"
  | "tool_result"
  | "turn_end"
  | "system"
  | "draft"
  | "error";

/** One normalized event from the agent's live reply. */
export interface LiveEvent {
  kind: LiveEventKind;
  text?: string;
  tool?: string;
  data?: Record<string, unknown>;
}

/** Every named SSE event we subscribe to; each carries the full LiveEvent as JSON. */
export const LIVE_EVENT_KINDS: LiveEventKind[] = [
  "text",
  "thinking",
  "tool_use",
  "tool_result",
  "turn_end",
  "system",
  "draft",
  "error",
];

export const prompterLiveApi = {
  /** Spawn the intake agent for a new chat. */
  start: async (payload: StartLivePayload): Promise<StartLiveResponse> => {
    const { data } = await api.post<StartLiveResponse>(
      "/prompter/live/start",
      payload
    );
    return data;
  },

  /** SSE URL the panel opens to watch the agent. EventSource sends no headers
   *  (the route is keyed by the opaque session id on the trusted network). */
  streamUrl: (sessionId: string): string =>
    `${API_URL}/prompter/live/${sessionId}/stream`,

  /** Is this session still running? The panel calls this after a reload to
   *  decide whether to reconnect the chat or fall back to the scope form. */
  status: async (sessionId: string): Promise<{ alive: boolean }> => {
    const { data } = await api.get<{ alive: boolean }>(
      `/prompter/live/${sessionId}/status`
    );
    return data;
  },

  /** Deliver the human's message to the running agent; the reply streams back. */
  sendMessage: async (sessionId: string, text: string): Promise<void> => {
    await api.post(`/prompter/live/${sessionId}/messages`, { text });
  },

  /** Reap the session (draft confirmed, or the human left the page). */
  stop: async (sessionId: string): Promise<void> => {
    await api.post(`/prompter/live/${sessionId}/stop`);
  },

  /** Confirm the draft → create the task and reap the agent (Phase 4 backend). */
  confirm: async (
    sessionId: string,
    payload: ConfirmPayload
  ): Promise<{ task_id: string }> => {
    const { data } = await api.post<{ task_id: string }>(
      `/prompter/live/${sessionId}/confirm`,
      payload
    );
    return data;
  },
};
