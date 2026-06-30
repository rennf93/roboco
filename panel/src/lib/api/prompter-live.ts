import api, { API_URL } from "./client";
import type {
  BatchConfirmPayload,
  BatchConfirmResult,
  BatchPreviewResult,
  ConfirmPayload,
  DraftProposal,
} from "./prompter";

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

/** Open a live chat scoped to exactly one of project / product / project_ids
 *  (a MegaTask spanning several possibly-unrelated repos). */
export interface StartLivePayload {
  project_id?: string;
  product_id?: string;
  project_ids?: string[];
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
  | "batch"
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
  "batch",
  "error",
];

export const prompterLiveApi = {
  /** Spawn the intake agent for a new chat. */
  start: async (payload: StartLivePayload): Promise<StartLiveResponse> => {
    const { data } = await api.post<StartLiveResponse>(
      "/prompter/live/start",
      payload,
    );
    return data;
  },

  /** Re-open intake to re-draft a board-reviewed task with the board's feedback.
   *  Spawns a fresh session seeded with the current draft + the board review. */
  reInterview: async (taskId: string): Promise<StartLiveResponse> => {
    const { data } = await api.post<StartLiveResponse>(
      `/prompter/live/re-interview/${taskId}`,
    );
    return data;
  },

  /** SSE URL the panel opens to watch the agent. EventSource cannot send custom
   *  headers, so the live-intake stream carries no `X-Agent-*` auth — the route
   *  is authenticated solely by the opaque, unguessable session id on the
   *  trusted internal network. By design: any session-id leakage grants stream
   *  access, so session ids must be treated as bearer credentials (never logged
   *  client-side, never put in a shareable URL). */
  streamUrl: (sessionId: string): string =>
    `${API_URL}/prompter/live/${sessionId}/stream`,

  /** Is this session still running? The panel calls this after a reload to
   *  decide whether to reconnect the chat or fall back to the scope form. */
  status: async (sessionId: string): Promise<{ alive: boolean }> => {
    const { data } = await api.get<{ alive: boolean }>(
      `/prompter/live/${sessionId}/status`,
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
    payload: ConfirmPayload,
  ): Promise<{ task_id: string }> => {
    const { data } = await api.post<{ task_id: string }>(
      `/prompter/live/${sessionId}/confirm`,
      payload,
    );
    return data;
  },

  /** Preview a MegaTask's waves without creating anything — for the human to
   *  review the sequencing before confirming. */
  previewBatch: async (
    sessionId: string,
    drafts: DraftProposal[],
  ): Promise<BatchPreviewResult> => {
    const { data } = await api.post<BatchPreviewResult>(
      `/prompter/live/${sessionId}/preview-batch`,
      { drafts },
    );
    return data;
  },

  /** Confirm a MegaTask → create the umbrella + sequenced root-subtasks, reap.
   *  Returns the umbrella id, the root-subtask ids, and the computed waves. */
  confirmBatch: async (
    sessionId: string,
    payload: BatchConfirmPayload,
  ): Promise<BatchConfirmResult> => {
    const { data } = await api.post<BatchConfirmResult>(
      `/prompter/live/${sessionId}/confirm-batch`,
      payload,
    );
    return data;
  },
};
