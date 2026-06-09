"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import { toast } from "sonner";
import {
  prompterLiveApi,
  LIVE_EVENT_KINDS,
  type LiveEvent,
} from "@/lib/api/prompter-live";
import {
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
  | "form" // collecting scope + opening message (no chat yet)
  | "preparing" // agent spawning / cloning the repo(s)
  | "chatting"
  | "streaming" // a reply is mid-flight over SSE
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

/** Which target the human picked for this chat. */
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
  // Structured spec fields
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

function newId(): string {
  return `msg-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
}

/** Remove the fenced ```roboco-draft block from displayed text — the structured
 *  draft card renders it; the raw JSON shouldn't sit in the chat bubble. */
function stripDraftFence(text: string): string {
  return text.replace(/```roboco-draft[\s\S]*?```/g, "").trimEnd();
}

/** Map an agent-proposed draft (the `draft` SSE event payload) to the editable
 *  form, carrying the chat's chosen scope through unchanged. */
function toEditable(
  draft: DraftProposal,
  scale: DraftScale | null,
  scope: { targetKind: TargetKind; projectId: string; productId: string }
): EditableDraft {
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
    // The scope picked up front wins; fall back to scale only if unset.
    targetKind:
      scope.targetKind || (scale === "multi" ? "product" : "project"),
    projectId: scope.projectId,
    productId: scope.productId,
  };
}

/** Pull a DraftProposal out of a `draft` SSE event's data payload. */
function draftFromEvent(data: Record<string, unknown> | undefined): {
  draft: DraftProposal;
  scale: DraftScale | null;
} | null {
  if (!data || typeof data !== "object") return null;
  const d = data as Record<string, unknown>;
  if (typeof d.title !== "string") return null;
  const scale =
    d.scale === "single" || d.scale === "multi"
      ? (d.scale as DraftScale)
      : null;
  return { draft: d as unknown as DraftProposal, scale };
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function usePrompter() {
  const [state, setState] = useState<PrompterState>("form");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [isSending, setIsSending] = useState(false);
  const [isLaunching, setIsLaunching] = useState(false);
  /** The latest tool the agent is using — "watch it work" status line. */
  const [activity, setActivity] = useState<string | null>(null);
  const [createdTaskId, setCreatedTaskId] = useState<string | null>(null);
  const [createdTaskTitle, setCreatedTaskTitle] = useState<string | null>(null);
  const [createdTaskTeam, setCreatedTaskTeam] = useState<Team | null>(null);

  // The up-front scope form.
  const [targetKind, setTargetKind] = useState<TargetKind>("project");
  const [projectId, setProjectId] = useState("");
  const [productId, setProductId] = useState("");
  const [initialMessage, setInitialMessage] = useState("");

  const [editableDraft, setEditableDraft] = useState<EditableDraft>(EMPTY_DRAFT);

  // Live-session plumbing held in refs so SSE callbacks never see stale state.
  const sessionIdRef = useRef<string | null>(null);
  const sourceRef = useRef<EventSource | null>(null);
  const streamingIdRef = useRef<string | null>(null);
  const scopeRef = useRef({ targetKind, projectId, productId });
  scopeRef.current = { targetKind, projectId, productId };

  // -----------------------------------------------------------------------
  // Message helpers
  // -----------------------------------------------------------------------

  const addMessage = useCallback((msg: Omit<ChatMessage, "id">) => {
    const id = newId();
    setMessages((prev) => [...prev, { ...msg, id }]);
    return id;
  }, []);

  /** Append a streamed token delta to the in-flight assistant message,
   *  starting a fresh one if this is the first delta of the turn. */
  const appendDelta = useCallback((delta: string) => {
    setMessages((prev) => {
      const id = streamingIdRef.current;
      if (id) {
        return prev.map((m) =>
          m.id === id ? { ...m, content: m.content + delta } : m
        );
      }
      const newMsgId = newId();
      streamingIdRef.current = newMsgId;
      return [...prev, { id: newMsgId, role: "assistant", content: delta }];
    });
  }, []);

  /** Attach the agent's proposed draft to the current/last assistant message,
   *  stripping the raw draft block out of that message's displayed text. */
  const attachDraft = useCallback((draft: DraftProposal) => {
    setMessages((prev) => {
      const targetId =
        streamingIdRef.current ??
        [...prev].reverse().find((m) => m.role === "assistant")?.id;
      if (targetId) {
        return prev.map((m) =>
          m.id === targetId
            ? { ...m, draft, content: stripDraftFence(m.content) }
            : m
        );
      }
      return [...prev, { id: newId(), role: "assistant", content: "", draft }];
    });
  }, []);

  // -----------------------------------------------------------------------
  // SSE handling
  // -----------------------------------------------------------------------

  const handleEvent = useCallback(
    (evt: LiveEvent) => {
      switch (evt.kind) {
        case "text":
          if (evt.text) {
            setActivity(null); // first text clears the "preparing…" indicator
            appendDelta(evt.text);
            setState("streaming");
          }
          break;
        case "tool_use":
          setActivity(evt.tool ? `Using ${evt.tool}…` : "Working…");
          break;
        case "thinking":
          setActivity("Thinking…");
          break;
        case "turn_end":
          streamingIdRef.current = null;
          setActivity(null);
          setIsSending(false);
          setState((s) => (s === "draft_preview" ? s : "chatting"));
          break;
        case "draft": {
          const parsed = draftFromEvent(evt.data);
          if (parsed) {
            attachDraft(parsed.draft);
            setEditableDraft(
              toEditable(parsed.draft, parsed.scale, scopeRef.current)
            );
            setState("draft_preview");
          }
          break;
        }
        case "error":
          streamingIdRef.current = null;
          setActivity(null);
          setIsSending(false);
          addMessage({
            role: "error",
            content: evt.text || "The agent hit an error.",
          });
          setState("chatting");
          break;
        // "system" / "tool_result" are informational — ignored in the UI.
        default:
          break;
      }
    },
    [appendDelta, attachDraft, addMessage]
  );

  const closeStream = useCallback(() => {
    sourceRef.current?.close();
    sourceRef.current = null;
  }, []);

  const openStream = useCallback(
    (sid: string) => {
      closeStream();
      const es = new EventSource(prompterLiveApi.streamUrl(sid));
      for (const kind of LIVE_EVENT_KINDS) {
        es.addEventListener(kind, (e: MessageEvent) => {
          try {
            handleEvent(JSON.parse(e.data) as LiveEvent);
          } catch {
            // A malformed frame is dropped; the stream stays open.
          }
        });
      }
      sourceRef.current = es;
    },
    [closeStream, handleEvent]
  );

  // Best-effort reap if the user navigates away mid-chat.
  useEffect(() => {
    return () => {
      closeStream();
      const sid = sessionIdRef.current;
      if (sid) void prompterLiveApi.stop(sid).catch(() => undefined);
    };
  }, [closeStream]);

  // -----------------------------------------------------------------------
  // Start the live session (from the scope form)
  // -----------------------------------------------------------------------

  const isFormValid = useCallback((): boolean => {
    const scoped = targetKind === "product" ? productId !== "" : projectId !== "";
    return scoped && initialMessage.trim().length > 0;
  }, [targetKind, projectId, productId, initialMessage]);

  const start = useCallback(async () => {
    if (!isFormValid() || state === "preparing") return;
    const opening = initialMessage.trim();
    setState("preparing");
    addMessage({ role: "user", content: opening });
    try {
      const { session_id } = await prompterLiveApi.start({
        ...(targetKind === "product"
          ? { product_id: productId }
          : { project_id: projectId }),
        initial_message: opening,
      });
      sessionIdRef.current = session_id;
      setSessionId(session_id);
      openStream(session_id);
      setIsSending(true); // the opening reply is on its way over SSE
      // start now returns immediately; the container spawns in the background
      // (clone + image build can take a minute). Show that until the first event.
      setActivity("Preparing the agent — cloning your repo and reading the code…");
      setState("streaming");
    } catch (err) {
      addMessage({ role: "error", content: getErrorMessage(err) });
      setState("form");
    }
  }, [
    isFormValid,
    state,
    initialMessage,
    targetKind,
    productId,
    projectId,
    addMessage,
    openStream,
  ]);

  // -----------------------------------------------------------------------
  // Send a chat message
  // -----------------------------------------------------------------------

  const send = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      const sid = sessionIdRef.current;
      if (!trimmed || isSending || !sid) return;

      setIsSending(true);
      setState("streaming");
      addMessage({ role: "user", content: trimmed });
      try {
        await prompterLiveApi.sendMessage(sid, trimmed);
        // The reply streams back over SSE; isSending clears on turn_end.
      } catch (err) {
        setIsSending(false);
        addMessage({ role: "error", content: getErrorMessage(err) });
        setState("chatting");
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

  const isValidForLaunch = useCallback((): boolean => {
    const base =
      editableDraft.title.trim().length > 0 &&
      editableDraft.description.trim().length >= 20 &&
      editableDraft.acceptance_criteria.length > 0;
    const targeted =
      editableDraft.targetKind === "product"
        ? editableDraft.productId !== ""
        : editableDraft.projectId !== "" && editableDraft.team !== "";
    return base && targeted;
  }, [editableDraft]);

  // -----------------------------------------------------------------------
  // Launch — confirm the draft → task, then reap the agent
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

    const effectiveTeam =
      editableDraft.targetKind === "product"
        ? Team.MAIN_PM
        : (editableDraft.team as Team);

    try {
      const { task_id } = await prompterLiveApi.confirm(sid, payload);
      // The draft became a task — reap the agent and close the stream.
      closeStream();
      void prompterLiveApi.stop(sid).catch(() => undefined);
      sessionIdRef.current = null;
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
  }, [editableDraft, isValidForLaunch, closeStream]);

  // -----------------------------------------------------------------------
  // Reset to start another conversation
  // -----------------------------------------------------------------------

  const startAnother = useCallback(() => {
    closeStream();
    const sid = sessionIdRef.current;
    if (sid) void prompterLiveApi.stop(sid).catch(() => undefined);
    sessionIdRef.current = null;
    streamingIdRef.current = null;
    setMessages([]);
    setSessionId(null);
    setActivity(null);
    setEditableDraft(EMPTY_DRAFT);
    setProjectId("");
    setProductId("");
    setInitialMessage("");
    setTargetKind("project");
    setCreatedTaskId(null);
    setCreatedTaskTitle(null);
    setCreatedTaskTeam(null);
    setState("form");
  }, [closeStream]);

  return {
    // State
    state,
    messages,
    sessionId,
    isSending,
    activity,
    editableDraft,
    createdTaskId,
    createdTaskTitle,
    createdTaskTeam,

    // Scope form
    targetKind,
    setTargetKind,
    projectId,
    setProjectId,
    productId,
    setProductId,
    initialMessage,
    setInitialMessage,
    isFormValid,
    start,

    // Chat + confirm
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
