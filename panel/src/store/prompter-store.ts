"use client";

import { create } from "zustand";
import { Team, TaskType, Complexity, TaskNature } from "@/types";

// =============================================================================
// Types
// =============================================================================

/** A draft field that tracks whether the user has edited it (dirty bit). */
export interface DraftField<T> {
  value: T;
  dirty: boolean;
}

/** Draft state for a TaskCreate payload built progressively by the LLM. */
export interface DraftState {
  title: DraftField<string>;
  description: DraftField<string>;
  team: DraftField<Team | null>;
  priority: DraftField<number>;
  acceptance_criteria: DraftField<string[]>;
  task_type: DraftField<TaskType>;
  estimated_complexity: DraftField<Complexity | null>;
  nature: DraftField<TaskNature | null>;
}

export type DraftFieldKey = keyof DraftState;

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: string;
}

export interface Conversation {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  messages: ChatMessage[];
  draft: DraftState;
  status: "active" | "launched" | "abandoned";
}

export type StreamingStatus = "idle" | "streaming" | "done" | "error";

function emptyDraft(): DraftState {
  return {
    title: { value: "", dirty: false },
    description: { value: "", dirty: false },
    team: { value: null, dirty: false },
    priority: { value: 2, dirty: false },
    acceptance_criteria: { value: [], dirty: false },
    task_type: { value: TaskType.CODE, dirty: false },
    estimated_complexity: { value: null, dirty: false },
    nature: { value: null, dirty: false },
  };
}

// =============================================================================
// Store Interface
// =============================================================================

interface PrompterState {
  /** ID of the currently active conversation */
  activeConversationId: string | null;

  /** Map of conversation ID → conversation data */
  conversations: Record<string, Conversation>;

  /** Current streaming status */
  streamingStatus: StreamingStatus;

  /** Model selected for chat */
  selectedModel: string;

  /** Whether the launch summary view is shown */
  showLaunchSummary: boolean;

  // ---- Actions ----

  /** Start a new conversation */
  startConversation: () => string;

  /** Open an existing conversation */
  openConversation: (id: string) => void;

  /** Add a user message to the active conversation */
  addUserMessage: (content: string) => ChatMessage;

  /** Append a token chunk to the last assistant message (or create it) */
  appendAssistantToken: (token: string) => void;

  /** Finalize the in-flight assistant message */
  finalizeAssistantMessage: () => void;

  /** Update a draft field from the LLM — respects dirty bit */
  setFieldFromLLM: (field: DraftFieldKey, value: DraftState[DraftFieldKey]["value"]) => void;

  /** Update a draft field from user input — marks it dirty */
  setFieldFromUser: (field: DraftFieldKey, value: DraftState[DraftFieldKey]["value"]) => void;

  /** Reset the dirty bit on a field (user cleared their edit) */
  clearFieldDirty: (field: DraftFieldKey) => void;

  /** Set streaming status */
  setStreamingStatus: (status: StreamingStatus) => void;

  /** Set selected model */
  setSelectedModel: (model: string) => void;

  /** Toggle launch summary */
  setShowLaunchSummary: (show: boolean) => void;

  /** Mark conversation as launched */
  markLaunched: (id: string) => void;

  /** Get active conversation */
  getActiveConversation: () => Conversation | null;
}

// =============================================================================
// Store Implementation
// =============================================================================

export const usePrompterStore = create<PrompterState>((set, get) => ({
  activeConversationId: null,
  conversations: {},
  streamingStatus: "idle",
  selectedModel: "claude-sonnet-4-5",
  showLaunchSummary: false,

  startConversation: () => {
    const id = `conv-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
    const now = new Date().toISOString();
    const conversation: Conversation = {
      id,
      title: "New conversation",
      createdAt: now,
      updatedAt: now,
      messages: [],
      draft: emptyDraft(),
      status: "active",
    };
    set((state) => ({
      conversations: { ...state.conversations, [id]: conversation },
      activeConversationId: id,
      showLaunchSummary: false,
    }));
    return id;
  },

  openConversation: (id: string) => {
    set({ activeConversationId: id, showLaunchSummary: false });
  },

  addUserMessage: (content: string) => {
    const { activeConversationId, conversations } = get();
    if (!activeConversationId) throw new Error("No active conversation");
    const msg: ChatMessage = {
      id: `msg-${Date.now()}`,
      role: "user",
      content,
      timestamp: new Date().toISOString(),
    };
    const conv = conversations[activeConversationId];
    const title = conv.messages.length === 0 ? content.slice(0, 60) : conv.title;
    set((state) => ({
      conversations: {
        ...state.conversations,
        [activeConversationId]: {
          ...conv,
          title,
          messages: [...conv.messages, msg],
          updatedAt: new Date().toISOString(),
        },
      },
    }));
    return msg;
  },

  appendAssistantToken: (token: string) => {
    const { activeConversationId, conversations } = get();
    if (!activeConversationId) return;
    const conv = conversations[activeConversationId];
    const messages = [...conv.messages];
    const last = messages[messages.length - 1];

    if (last && last.role === "assistant") {
      messages[messages.length - 1] = { ...last, content: last.content + token };
    } else {
      messages.push({
        id: `msg-${Date.now()}`,
        role: "assistant",
        content: token,
        timestamp: new Date().toISOString(),
      });
    }

    set((state) => ({
      conversations: {
        ...state.conversations,
        [activeConversationId]: {
          ...conv,
          messages,
          updatedAt: new Date().toISOString(),
        },
      },
    }));
  },

  finalizeAssistantMessage: () => {
    const { activeConversationId, conversations } = get();
    if (!activeConversationId) return;
    const conv = conversations[activeConversationId];
    set((state) => ({
      conversations: {
        ...state.conversations,
        [activeConversationId]: {
          ...conv,
          updatedAt: new Date().toISOString(),
        },
      },
    }));
  },

  setFieldFromLLM: (field, value) => {
    const { activeConversationId, conversations } = get();
    if (!activeConversationId) return;
    const conv = conversations[activeConversationId];
    // DIRTY BIT PROTECTION: only update if user hasn't edited this field
    if (conv.draft[field].dirty) return;
    set((state) => ({
      conversations: {
        ...state.conversations,
        [activeConversationId]: {
          ...conv,
          draft: {
            ...conv.draft,
            [field]: { value, dirty: false },
          },
          updatedAt: new Date().toISOString(),
        },
      },
    }));
  },

  setFieldFromUser: (field, value) => {
    const { activeConversationId, conversations } = get();
    if (!activeConversationId) return;
    const conv = conversations[activeConversationId];
    set((state) => ({
      conversations: {
        ...state.conversations,
        [activeConversationId]: {
          ...conv,
          draft: {
            ...conv.draft,
            [field]: { value, dirty: true },
          },
          updatedAt: new Date().toISOString(),
        },
      },
    }));
  },

  clearFieldDirty: (field) => {
    const { activeConversationId, conversations } = get();
    if (!activeConversationId) return;
    const conv = conversations[activeConversationId];
    set((state) => ({
      conversations: {
        ...state.conversations,
        [activeConversationId]: {
          ...conv,
          draft: {
            ...conv.draft,
            [field]: { ...conv.draft[field], dirty: false },
          },
        },
      },
    }));
  },

  setStreamingStatus: (status) => set({ streamingStatus: status }),

  setSelectedModel: (model) => set({ selectedModel: model }),

  setShowLaunchSummary: (show) => set({ showLaunchSummary: show }),

  markLaunched: (id) => {
    set((state) => ({
      conversations: {
        ...state.conversations,
        [id]: { ...state.conversations[id], status: "launched" },
      },
    }));
  },

  getActiveConversation: () => {
    const { activeConversationId, conversations } = get();
    if (!activeConversationId) return null;
    return conversations[activeConversationId] ?? null;
  },
}));
