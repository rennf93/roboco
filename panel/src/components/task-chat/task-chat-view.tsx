"use client";

import { useState, useCallback, useId } from "react";
import { toast } from "sonner";
import { TaskStatus } from "@/types";
import { ConversationHistory, ConversationHistoryItem } from "./conversation-history";
import { ChatPanel, ChatMessage, DraftUpdate } from "./chat-panel";
import { DraftPanel } from "./draft-panel";
import { DraftState } from "./draft-status-badge";
import { DEFAULT_MODEL } from "./model-selector";

// ---------------------------------------------------------------------------
// Mock data for the conversation history
// In production, this would be fetched from the API with useQuery.
// ---------------------------------------------------------------------------
const MOCK_HISTORY: ConversationHistoryItem[] = [
  {
    id: "conv-1",
    taskTitle: "Implement JWT refresh token rotation",
    status: TaskStatus.COMPLETED,
    lastActivityAt: new Date(Date.now() - 3 * 24 * 3600_000).toISOString(),
    messageCount: 12,
  },
  {
    id: "conv-2",
    taskTitle: "Design dual-panel task creation UI",
    status: TaskStatus.IN_PROGRESS,
    lastActivityAt: new Date(Date.now() - 2 * 3600_000).toISOString(),
    messageCount: 7,
  },
  {
    id: "conv-3",
    taskTitle: "Add model selector to chat header",
    status: TaskStatus.AWAITING_QA,
    lastActivityAt: new Date(Date.now() - 45 * 60_000).toISOString(),
    messageCount: 5,
  },
  {
    id: "conv-4",
    taskTitle: "Refactor kanban board to use shadcn/ui primitives",
    status: TaskStatus.PENDING,
    lastActivityAt: new Date(Date.now() - 10 * 60_000).toISOString(),
    messageCount: 2,
  },
];

// ---------------------------------------------------------------------------
// Simulate an AI response for prototyping.
// In production this would be replaced by a streaming API call.
// ---------------------------------------------------------------------------
async function simulateAIResponse(
  userMessage: string
): Promise<{ reply: string; draftUpdate: DraftUpdate }> {
  await new Promise((r) => setTimeout(r, 1200 + Math.random() * 800));

  const lower = userMessage.toLowerCase();

  // Simple keyword-based heuristic to generate a plausible draft
  if (lower.includes("auth") || lower.includes("login") || lower.includes("jwt")) {
    return {
      reply:
        "I've extracted a task draft from your description. I've set the team to 'backend' since this is an authentication concern. Would you like to add more acceptance criteria or adjust the priority?",
      draftUpdate: {
        title: "Implement secure authentication with JWT",
        description:
          "Set up JSON Web Token (JWT) based authentication including access token issuance, refresh token rotation, and secure storage patterns.",
        acceptanceCriteria: [
          "Users can log in with email + password and receive a JWT access token",
          "Refresh tokens rotate on each use and expire after 30 days",
          "Invalid or expired tokens return a 401 response with a clear error message",
          "All auth endpoints are rate-limited to prevent brute-force attacks",
        ],
        team: "backend",
        priority: "P1 – High",
      },
    };
  }

  if (lower.includes("ui") || lower.includes("design") || lower.includes("component")) {
    return {
      reply:
        "Got it! I've drafted a UX/UI task. I've left the acceptance criteria open-ended — should I make them more specific? For example, I can add accessibility requirements or specific Tailwind token constraints.",
      draftUpdate: {
        title: "Design and implement new UI component",
        description: userMessage,
        acceptanceCriteria: [
          "Component renders correctly in both light and dark modes",
          "All interactive elements are keyboard-navigable (WCAG 2.1 AA)",
          "Uses shadcn/ui primitives and existing Tailwind design tokens",
          "No new colors or animation libraries introduced",
        ],
        team: "ux_ui",
        priority: "P2 – Medium",
      },
    };
  }

  if (lower.includes("bug") || lower.includes("fix") || lower.includes("error")) {
    return {
      reply:
        "I've created a bug-fix task draft. I've set priority to High since bugs usually require prompt attention — adjust if needed. Want me to add reproduction steps to the acceptance criteria?",
      draftUpdate: {
        title: `Fix: ${userMessage.slice(0, 60)}`,
        description: `Bug report: ${userMessage}`,
        acceptanceCriteria: [
          "The described bug is no longer reproducible in the test environment",
          "A regression test is added to prevent re-occurrence",
          "No existing tests are broken by the fix",
        ],
        team: "backend",
        priority: "P1 – High",
      },
    };
  }

  // Generic fallback
  return {
    reply:
      "I've started a task draft based on your description. The title and description are populated — can you clarify what the acceptance criteria should be? For example: what does 'done' look like for this task?",
    draftUpdate: {
      title: userMessage.slice(0, 80),
      description: userMessage,
      team: "backend",
      priority: "P2 – Medium",
    },
  };
}

// ---------------------------------------------------------------------------
// Main view
// ---------------------------------------------------------------------------

/**
 * TaskChatView — the top-level component for the /task-chat page.
 *
 * Layout (left → right):
 *   ┌──────────────────┬──────────────────────────┬──────────────────────┐
 *   │ Conversation     │ Chat panel               │ Task draft panel     │
 *   │ history sidebar  │ (messages + model sel.)  │ (structured output)  │
 *   │ (fixed 280px)    │ (flex-1)                 │ (flex-1)             │
 *   └──────────────────┴──────────────────────────┴──────────────────────┘
 *
 * The history sidebar provides navigation between past conversations.
 * The chat panel is the conversational input area.
 * The draft panel is the structured output that updates as the chat progresses.
 */
export function TaskChatView() {
  const uid = useId();

  // ── State ─────────────────────────────────────────────────────────────────

  const [convHistory, setConvHistory] = useState<ConversationHistoryItem[]>(MOCK_HISTORY);
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputValue, setInputValue] = useState("");
  const [isGenerating, setIsGenerating] = useState(false);
  const [selectedModel, setSelectedModel] = useState(DEFAULT_MODEL);

  const [draft, setDraft] = useState<DraftUpdate>({});
  const [draftState, setDraftState] = useState<DraftState>("still-refining");

  // ── Handlers ──────────────────────────────────────────────────────────────

  const handleSend = useCallback(async () => {
    const trimmed = inputValue.trim();
    if (!trimmed || isGenerating) return;

    const userMsg: ChatMessage = {
      id: `${uid}-user-${Date.now()}`,
      role: "user",
      content: trimmed,
      timestamp: new Date().toISOString(),
    };

    setMessages((prev) => [...prev, userMsg]);
    setInputValue("");
    setIsGenerating(true);
    setDraftState("still-refining");

    try {
      const { reply, draftUpdate } = await simulateAIResponse(trimmed);

      const assistantMsg: ChatMessage = {
        id: `${uid}-assistant-${Date.now()}`,
        role: "assistant",
        content: reply,
        timestamp: new Date().toISOString(),
      };

      setMessages((prev) => [...prev, assistantMsg]);

      // Merge new draft fields into existing draft
      setDraft((prev) => ({ ...prev, ...draftUpdate }));

      // Once we have at least one assistant response, the draft is ready for review
      setDraftState("draft-ready-for-review");
    } catch {
      toast.error("Failed to get AI response. Please try again.");
    } finally {
      setIsGenerating(false);
    }
  }, [inputValue, isGenerating, uid]);

  const handleNewConversation = useCallback(() => {
    setSelectedConversationId(null);
    setMessages([]);
    setDraft({});
    setDraftState("still-refining");
    setInputValue("");
  }, []);

  const handleContinue = useCallback(
    (id: string) => {
      setSelectedConversationId(id);
      // In production: load messages from API
      toast.info("Loading conversation…");
    },
    []
  );

  const handleReuse = useCallback(
    (id: string) => {
      const item = convHistory.find((h) => h.id === id);
      if (!item) return;
      setSelectedConversationId(null);
      setMessages([]);
      setDraft({ title: item.taskTitle });
      setDraftState("still-refining");
      setInputValue("");
      toast.info(`Pre-seeded draft with "${item.taskTitle}" — refine it in the chat.`);
    },
    [convHistory]
  );

  const handleSubmitDraft = useCallback(() => {
    if (!draft.title) return;

    // Add to history
    const newItem: ConversationHistoryItem = {
      id: `conv-${Date.now()}`,
      taskTitle: draft.title,
      status: TaskStatus.PENDING,
      lastActivityAt: new Date().toISOString(),
      messageCount: messages.length,
    };
    setConvHistory((prev) => [newItem, ...prev]);

    toast.success("Draft submitted for review!", {
      description: "A PM will review and assign the task.",
    });

    handleNewConversation();
  }, [draft, messages, handleNewConversation]);

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="space-y-4">
      {/* Page header */}
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Task Chat</h1>
        <p className="text-muted-foreground">
          Describe a task in natural language — the AI will structure it into a draft ready for review.
        </p>
      </div>

      {/*
       * THREE-PANEL LAYOUT
       * Height is calculated to fill the remaining viewport below the page header.
       * The pattern mirrors communications-view.tsx which uses h-[calc(100vh-220px)].
       */}
      <div className="grid h-[calc(100vh-200px)] gap-4"
           style={{ gridTemplateColumns: "280px 1fr 1fr" }}>

        {/* ── Panel 1: Conversation History ──────────────────────────────── */}
        <ConversationHistory
          items={convHistory}
          selectedId={selectedConversationId}
          onContinue={handleContinue}
          onReuse={handleReuse}
          onNewConversation={handleNewConversation}
        />

        {/* ── Panel 2: Chat ──────────────────────────────────────────────── */}
        <ChatPanel
          messages={messages}
          inputValue={inputValue}
          selectedModel={selectedModel}
          isGenerating={isGenerating}
          onInputChange={setInputValue}
          onSend={handleSend}
          onModelChange={setSelectedModel}
        />

        {/* ── Panel 3: Task Draft ────────────────────────────────────────── */}
        <DraftPanel
          draft={draft}
          draftState={draftState}
          onSubmitDraft={handleSubmitDraft}
        />
      </div>
    </div>
  );
}
