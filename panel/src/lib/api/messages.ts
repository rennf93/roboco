import api from "./client";
import type { Message, MessageType } from "@/types";
import { isMockMode, getMockMessages, AGENT_IDS, CHANNEL_IDS } from "@/lib/mock-data";

// Store for mock messages (persists during session)
let mockMessagesStore: Message[] | null = null;
const getMessages = (): Message[] => {
  if (!mockMessagesStore) {
    mockMessagesStore = getMockMessages() as Message[];
  }
  return mockMessagesStore;
};

export const messagesApi = {
  // List messages for a session
  listBySession: async (
    sessionId: string,
    limit: number = 50,
    before?: string,
    after?: string
  ): Promise<{ items: Message[]; has_more: boolean }> => {
    if (isMockMode()) {
      let messages = getMessages().filter(
        (m) => m.session_id === sessionId
      );
      if (before) {
        messages = messages.filter(
          (m) => new Date(m.timestamp) < new Date(before)
        );
      }
      if (after) {
        messages = messages.filter(
          (m) => new Date(m.timestamp) > new Date(after)
        );
      }
      return {
        items: messages.slice(0, limit),
        has_more: messages.length > limit,
      };
    }
    const { data } = await api.get<{ items: Message[]; has_more: boolean }>("/messages", {
      params: { session_id: sessionId, limit, before, after },
    });
    return data;
  },

  // Get message by ID
  get: async (messageId: string): Promise<Message> => {
    if (isMockMode()) {
      const message = getMessages().find((m) => m.id === messageId);
      if (message) return message;
      throw new Error("Message not found");
    }
    const { data } = await api.get<Message>("/messages/" + messageId);
    return data;
  },

  // Send a message
  send: async (sessionId: string, content: string, type: string = "dialogue"): Promise<Message> => {
    if (isMockMode()) {
      const newMessage: Message = {
        id: `msg-${Date.now()}`,
        agent_id: AGENT_IDS.ceo,
        channel_id: CHANNEL_IDS.backendCell,
        group_id: `msg-${Date.now()}`,
        session_id: sessionId,
        type: type as MessageType,
        content,
        content_length: content.length,
        is_reply: false,
        reply_to: null,
        mentions: [],
        task_id: null,
        commit_ref: null,
        timestamp: new Date().toISOString(),
        edited_at: null,
        was_edited: false,
      };
      getMessages().push(newMessage);
      return newMessage;
    }
    const { data } = await api.post<Message>("/messages", {
      session_id: sessionId,
      content,
      type,
    });
    return data;
  },

  // Edit a message
  edit: async (messageId: string, content: string): Promise<Message> => {
    if (isMockMode()) {
      const messages = getMessages();
      const idx = messages.findIndex((m) => m.id === messageId);
      if (idx !== -1) {
        const message = messages[idx];
        const editedMessage: Message = {
          ...message,
          content,
          content_length: content.length,
          edited_at: new Date().toISOString(),
          was_edited: true,
        };
        messages[idx] = editedMessage;
        return editedMessage;
      }
      throw new Error("Message not found");
    }
    const { data } = await api.patch<Message>("/messages/" + messageId, { content });
    return data;
  },

  // Delete a message
  delete: async (messageId: string): Promise<void> => {
    if (isMockMode()) {
      const messages = getMessages();
      const idx = messages.findIndex((m) => m.id === messageId);
      if (idx !== -1) messages.splice(idx, 1);
      return;
    }
    await api.delete("/messages/" + messageId);
  },
};
