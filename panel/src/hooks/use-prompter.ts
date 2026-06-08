"use client";

import { useState, useCallback, useRef } from "react";
import { toast } from "sonner";
import {
  prompterApi,
  type DraftProposal,
  type CellWork,
  type DraftScale,
  type ConfirmPayload,
} from "@/lib/api/prompter";
import { getErrorMessage } from "@/lib/api/client";
import { Team } from "@/types";
import type { TaskType, TaskNature, Complexity } from "@/types";

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

/** Which target the human picked for this task. */
export type TargetKind = "project" | "product";

export interface EditableDraft {
  title: string;
  description: string;
  acceptance_criteria: string[];
  team: Team | "";
  priority: number;
  task_type: TaskType | "";
  nature: TaskNature | "";
  estimated_complexity: Complexity | "";
  // Structured GOLD fields
  objective: string;
  what_this_builds: string[];
  the_work: CellWork[];
  notes: string[];
  // Targeting
  targetKind: TargetKind;
  projectId: string;
  productId: string;
}

const EMPTY_DRAFT: EditableDraft = {
  title: "",
  description: "",
  acceptance_criteria: [],
  team: "",
  priority: 2,
  task_type: "",
  nature: "",
  estimated_complexity: "",
  objective: "",
  what_this_builds: [],
  the_work: [],
  notes: [],
  targetKind: "project",
  projectId: "",
  productId: "",
};

function toEditable(draft: DraftProposal, scale: DraftScale | null): EditableDraft {
  return {
    title: draft.title,
    description: draft.description,
    acceptance_criteria: draft.acceptance_criteria,
    team: draft.team ?? "",
    priority: draft.priority ?? 2,
    task_type: draft.task_type ?? "",
    nature: draft.nature ?? "",
    estimated_complexity: draft.estimated_complexity ?? "",
    objective: draft.objective ?? "",
    what_this_builds: draft.what_this_builds ?? [],
    the_work: draft.the_work ?? [],
    notes: draft.notes ?? [],
    // A multi-cell feature defaults to picking a Product; single-cell a Project.
    targetKind: scale === "multi" ? "product" : "project",
    projectId: "",
    productId: "",
  };
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function usePrompter() {
  const [state, setState] = useState<PrompterState>("empty");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [isSending, setIsSending] = useState(false);
  const [isLaunching, setIsLaunching] = useState(false);
  const [createdTaskId, setCreatedTaskId] = useState<string | null>(null);
  const [createdTaskTitle, setCreatedTaskTitle] = useState<string | null>(null);
  const [createdTaskTeam, setCreatedTaskTeam] = useState<Team | null>(null);

  /** Draft as shown in the draft-preview card */
  const [draftProposal, setDraftProposal] = useState<DraftProposal | null>(null);

  /** Editable copy used in the confirmation dialog */
  const [editableDraft, setEditableDraft] = useState<EditableDraft>(EMPTY_DRAFT);

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
      addMessage({ role: "user", content: text.trim() });

      try {
        let sid = sessionIdRef.current;
        if (!sid) {
          const { session_id } = await prompterApi.createSession();
          sid = session_id;
          sessionIdRef.current = sid;
          setSessionId(sid);
        }

        const response = await prompterApi.sendMessage(sid, text.trim());

        if (response.draft) {
          addMessage({
            role: "assistant",
            content: response.reply,
            draft: response.draft,
          });
          setDraftProposal(response.draft);
          setEditableDraft(toEditable(response.draft, response.scale));
          setState("draft_preview");
        } else {
          addMessage({ role: "assistant", content: response.reply });
          setState("chatting");
        }
      } catch (err) {
        addMessage({ role: "error", content: getErrorMessage(err) });
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

  const openReview = useCallback(() => setState("review_modal"), []);
  const closeReview = useCallback(() => setState("draft_preview"), []);
  const keepChatting = useCallback(() => setState("chatting"), []);

  const updateDraft = useCallback((updates: Partial<EditableDraft>) => {
    setEditableDraft((prev) => ({ ...prev, ...updates }));
  }, []);

  // -----------------------------------------------------------------------
  // Validation
  // -----------------------------------------------------------------------

  const isValidForLaunch = useCallback((): boolean => {
    const base =
      editableDraft.title.trim().length > 0 &&
      editableDraft.description.trim().length >= 20 &&
      editableDraft.acceptance_criteria.length > 0;
    // A board-led feature needs a product; a single-cell task needs a project
    // and a cell team.
    const targeted =
      editableDraft.targetKind === "product"
        ? editableDraft.productId !== ""
        : editableDraft.projectId !== "" && editableDraft.team !== "";
    return base && targeted;
  }, [editableDraft]);

  // -----------------------------------------------------------------------
  // Launch — create the task through the Prompter confirm endpoint
  // -----------------------------------------------------------------------

  const launchTask = useCallback(async () => {
    const sid = sessionIdRef.current;
    if (!sid || !isValidForLaunch()) return;

    setIsLaunching(true);
    setState("launching");

    const draft: DraftProposal = {
      title: editableDraft.title.trim(),
      description: editableDraft.description.trim(),
      acceptance_criteria: editableDraft.acceptance_criteria,
      team: editableDraft.team as Team,
      priority: editableDraft.priority,
      objective: editableDraft.objective.trim() || null,
      what_this_builds: editableDraft.what_this_builds,
      the_work: editableDraft.the_work,
      notes: editableDraft.notes,
      ...(editableDraft.task_type ? { task_type: editableDraft.task_type } : {}),
      ...(editableDraft.nature ? { nature: editableDraft.nature } : {}),
      ...(editableDraft.estimated_complexity
        ? { estimated_complexity: editableDraft.estimated_complexity }
        : {}),
    };

    const payload: ConfirmPayload =
      editableDraft.targetKind === "product"
        ? { product_id: editableDraft.productId, draft }
        : { project_id: editableDraft.projectId, draft };

    // A product target is routed through the Main PM; a project target stays
    // with the chosen cell.
    const effectiveTeam =
      editableDraft.targetKind === "product"
        ? Team.MAIN_PM
        : (editableDraft.team as Team);

    try {
      const { task_id } = await prompterApi.confirm(sid, payload);
      setCreatedTaskId(task_id);
      setCreatedTaskTitle(draft.title);
      setCreatedTaskTeam(effectiveTeam);
      toast.success("Task created and launched!");
      setState("success");
    } catch (err) {
      toast.error(`Failed to launch task: ${getErrorMessage(err)}`);
      setState("review_modal");
    } finally {
      setIsLaunching(false);
    }
  }, [editableDraft, isValidForLaunch]);

  // -----------------------------------------------------------------------
  // Reset to start another conversation
  // -----------------------------------------------------------------------

  const startAnother = useCallback(() => {
    setMessages([]);
    setSessionId(null);
    sessionIdRef.current = null;
    setDraftProposal(null);
    setEditableDraft(EMPTY_DRAFT);
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
    isLaunching,
  };
}
