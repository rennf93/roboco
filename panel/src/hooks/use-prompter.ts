"use client";

import { useState, useCallback, useRef } from "react";
import { toast } from "sonner";
import { prompterApi, type DraftProposal } from "@/lib/api/prompter";
import { getErrorMessage } from "@/lib/api/client";
import { useCreateTask } from "@/hooks/use-tasks";
import type { TaskCreate, Team, TaskType, Complexity } from "@/types";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type PrompterState =
  | "empty"
  | "chatting"
  | "draft_preview"
  | "review_modal"
  | "launching"
  | "success";

export type MessageRole = "user" | "assistant" | "error";

export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  /** Present only on an assistant message that contains a draft proposal */
  draft?: DraftProposal;
}

export interface EditableDraft {
  title: string;
  description: string;
  acceptance_criteria: string[];
  team: Team | "";
  priority: number;
  task_type: TaskType | "";
  estimated_complexity: Complexity | "";
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function usePrompter() {
  const createTask = useCreateTask();

  const [state, setState] = useState<PrompterState>("empty");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [isSending, setIsSending] = useState(false);
  const [createdTaskId, setCreatedTaskId] = useState<string | null>(null);
  const [createdTaskTitle, setCreatedTaskTitle] = useState<string | null>(null);
  const [createdTaskTeam, setCreatedTaskTeam] = useState<Team | null>(null);

  /** Draft as shown in the draft-preview card */
  const [draftProposal, setDraftProposal] = useState<DraftProposal | null>(null);

  /** Editable copy used in the confirmation dialog */
  const [editableDraft, setEditableDraft] = useState<EditableDraft>({
    title: "",
    description: "",
    acceptance_criteria: [],
    team: "",
    priority: 2,
    task_type: "",
    estimated_complexity: "",
  });

  // Keep a ref to sessionId for callbacks to avoid stale closures
  const sessionIdRef = useRef<string | null>(null);

  // -----------------------------------------------------------------------
  // Helpers
  // -----------------------------------------------------------------------

  const addMessage = useCallback((msg: Omit<ChatMessage, "id">) => {
    const id = `msg-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    setMessages((prev) => [...prev, { ...msg, id }]);
    return id;
  }, []);

  // -----------------------------------------------------------------------
  // Send a chat message
  // -----------------------------------------------------------------------

  const send = useCallback(
    async (text: string) => {
      if (!text.trim() || isSending) return;

      setIsSending(true);
      setState("chatting");

      // Add user message to chat
      addMessage({ role: "user", content: text.trim() });

      try {
        let sid = sessionIdRef.current;

        // Create session on first message
        if (!sid) {
          const { session_id } = await prompterApi.createSession();
          sid = session_id;
          sessionIdRef.current = sid;
          setSessionId(sid);
        }

        // Send message and get reply
        const response = await prompterApi.sendMessage(sid, text.trim());

        if (response.draft) {
          // LLM produced a draft — add assistant message with embedded draft
          addMessage({
            role: "assistant",
            content: response.reply,
            draft: response.draft,
          });
          setDraftProposal(response.draft);
          setEditableDraft({
            title: response.draft.title,
            description: response.draft.description,
            acceptance_criteria: response.draft.acceptance_criteria,
            team: response.draft.team ?? "",
            priority: response.draft.priority ?? 2,
            task_type: response.draft.task_type ?? "",
            estimated_complexity: response.draft.estimated_complexity ?? "",
          });
          setState("draft_preview");
        } else {
          // Plain text reply
          addMessage({ role: "assistant", content: response.reply });
          setState("chatting");
        }
      } catch (err) {
        const msg = getErrorMessage(err);
        addMessage({
          role: "error",
          content: msg,
        });
        setState("chatting");
      } finally {
        setIsSending(false);
      }
    },
    [isSending, addMessage]
  );

  // -----------------------------------------------------------------------
  // Review & Confirm actions
  // -----------------------------------------------------------------------

  const openReview = useCallback(() => {
    setState("review_modal");
  }, []);

  const closeReview = useCallback(() => {
    setState("draft_preview");
  }, []);

  const keepChatting = useCallback(() => {
    setState("chatting");
  }, []);

  const updateDraft = useCallback((updates: Partial<EditableDraft>) => {
    setEditableDraft((prev) => ({ ...prev, ...updates }));
  }, []);

  // -----------------------------------------------------------------------
  // Validation
  // -----------------------------------------------------------------------

  const isValidForLaunch = useCallback((): boolean => {
    return (
      editableDraft.title.trim().length > 0 &&
      editableDraft.description.trim().length >= 20 &&
      editableDraft.acceptance_criteria.length > 0 &&
      editableDraft.team !== ""
    );
  }, [editableDraft]);

  // -----------------------------------------------------------------------
  // Launch (create task)
  // -----------------------------------------------------------------------

  const launchTask = useCallback(async () => {
    if (!isValidForLaunch()) return;

    setState("launching");

    const payload: TaskCreate = {
      title: editableDraft.title.trim(),
      description: editableDraft.description.trim(),
      acceptance_criteria: editableDraft.acceptance_criteria,
      team: editableDraft.team as Team,
      priority: editableDraft.priority,
      ...(editableDraft.task_type ? { task_type: editableDraft.task_type as TaskType } : {}),
      ...(editableDraft.estimated_complexity
        ? { estimated_complexity: editableDraft.estimated_complexity as Complexity }
        : {}),
    };

    try {
      const task = await createTask.mutateAsync(payload);
      setCreatedTaskId(task.id);
      setCreatedTaskTitle(task.title);
      setCreatedTaskTeam(task.team as Team);
      toast.success("Task created successfully!");
      setState("success");
    } catch (err) {
      const msg = getErrorMessage(err);
      toast.error(`Failed to create task: ${msg}`);
      setState("review_modal");
    }
  }, [editableDraft, isValidForLaunch, createTask]);

  // -----------------------------------------------------------------------
  // Reset to start another conversation
  // -----------------------------------------------------------------------

  const startAnother = useCallback(() => {
    setMessages([]);
    setSessionId(null);
    sessionIdRef.current = null;
    setDraftProposal(null);
    setEditableDraft({
      title: "",
      description: "",
      acceptance_criteria: [],
      team: "",
      priority: 2,
      task_type: "",
      estimated_complexity: "",
    });
    setCreatedTaskId(null);
    setCreatedTaskTitle(null);
    setCreatedTaskTeam(null);
    setState("empty");
  }, []);

  return {
    // State
    state,
    messages,
    sessionId,
    isSending,
    draftProposal,
    editableDraft,
    createdTaskId,
    createdTaskTitle,
    createdTaskTeam,

    // Actions
    send,
    openReview,
    closeReview,
    keepChatting,
    updateDraft,
    isValidForLaunch,
    launchTask,
    startAnother,
    isLaunching: createTask.isPending,
  };
}
