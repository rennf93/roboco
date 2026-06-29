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
  type BatchConfirmResult,
} from "@/lib/api/prompter";
import { getErrorMessage } from "@/lib/api/client";
import { tasksApi } from "@/lib/api/tasks";
import { useProjects } from "@/hooks/use-projects";
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
  | "batch_preview" // a MegaTask (N drafts) is ready to review
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
export type TargetKind = "project" | "product" | "megatask";

/** A MegaTask the agent proposed: a title + one draft per task (each draft
 *  carries its own project_id + collision surface). `dropped` is how many raw
 *  entries the agent emitted that were malformed and discarded.
 *
 *  ``parkedCellWork`` is client-only state (never sent to the backend — confirm
 *  ships only ``title``/``drafts``/``project_ids``/``route``): a snapshot of each
 *  draft's last per-cell content, keyed by draft index. It exists so toggling a
 *  cell's project OFF then back ON in the review card restores the agent-authored
 *  / user-edited summary+items instead of blanking them (F086). */
export interface BatchProposal {
  title: string;
  drafts: DraftProposal[];
  dropped: number;
  parkedCellWork?: Record<number, CellWork[]>;
}

/** Which start button the human pressed on the draft card. */
export type StartRoute = "board" | "main_pm";

/** The delivery cells that carry their own per-cell project in a MegaTask draft.
 *  A RoboCo project is per-cell (assigned_cell); a multi-cell draft puts one
 *  the_work entry per cell, each with its cell's project_id. */
const CELL_TEAMS: Team[] = [Team.BACKEND, Team.FRONTEND, Team.UX_UI];

/** The per-cell project_ids a draft targets: one per the_work entry whose team
 *  is a delivery cell and that carries a project_id. Empty for a legacy
 *  single-cell draft that uses a top-level project_id instead. */
function draftCellProjectIds(draft: DraftProposal): string[] {
  const work = Array.isArray(draft.the_work) ? draft.the_work : [];
  return work
    .filter(
      (w): w is CellWork & { project_id: string } =>
        !!w?.team &&
        (CELL_TEAMS as readonly string[]).includes(w.team) &&
        typeof w.project_id === "string" &&
        w.project_id !== "",
    )
    .map((w) => w.project_id);
}

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
  scope: { targetKind: TargetKind; projectId: string; productId: string },
): EditableDraft {
  return {
    title: draft.title,
    // The prompter's propose_draft schema has NO `description` field — it uses
    // `objective` + the structured spec. Fall back to objective so launch
    // validation never reads `undefined` (which threw on `.trim()` and silently
    // killed the button click).
    description: draft.description ?? draft.objective ?? "",
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
    targetKind: scope.targetKind || (scale === "multi" ? "product" : "project"),
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

/** Coerce a value into a `CellWork[]`: a bare object → one-element array, a
 *  non-array non-object → empty. The backend coerces `the_work` before it
 *  reaches SSE, but a stale localStorage payload or a malformed frame could
 *  still carry a non-array, and the batch card does `the_work.map(...)` — so
 *  normalize here rather than crash. */
function asCellWork(value: unknown): CellWork[] {
  if (Array.isArray(value)) return value as CellWork[];
  if (value && typeof value === "object") return [value as CellWork];
  return [];
}

/** Pull a MegaTask ({title, drafts[]}) out of a `batch` SSE event's payload. */
function batchFromEvent(
  data: Record<string, unknown> | undefined,
): BatchProposal | null {
  if (!data || typeof data !== "object") return null;
  const raw = (data as Record<string, unknown>).drafts;
  if (!Array.isArray(raw)) return null;
  const drafts = raw
    .filter(
      (x): x is Record<string, unknown> =>
        !!x && typeof (x as DraftProposal).title === "string",
    )
    .map((d) => ({
      ...(d as unknown as DraftProposal),
      the_work: asCellWork((d as Record<string, unknown>).the_work),
    }));
  if (drafts.length === 0) return null;
  const title = (data as Record<string, unknown>).title;
  // Prefer the backend's dropped count; else compute from what we filtered, so a
  // shrunk batch is surfaced rather than silently delivering fewer tasks.
  const backendDropped = (data as Record<string, unknown>).dropped;
  const dropped =
    typeof backendDropped === "number"
      ? backendDropped
      : raw.length - drafts.length;
  return { title: typeof title === "string" ? title : "", drafts, dropped };
}

/** Rebuild a draft's ``the_work`` from the set of projects the human selected
 *  for it (the multi-select review card: one task can span several repos, one
 *  repo per delivery cell — the backend's ``task_cell_projects`` is unique per
 *  ``(task, team)``). Existing cell entries keep their summary/items; a newly-
 *  selected cell gets a minimal entry; a deselected cell's entry is dropped
 *  (that cell no longer participates). The top-level ``project_id`` is cleared
 *  — a multi-repo task targets via its cell map, not a top-level repo. Pure, so
 *  ``setBatchDraftProjects`` can stay a thin setState wrapper and this is
 *  unit-tested directly.
 *
 *  ``priorByCell`` is the parked snapshot of a cell's last content (kept by the
 *  caller in ``BatchProposal.parkedCellWork``) so that toggling a cell's project
 *  OFF then back ON restores the agent-authored / user-edited summary+items
 *  instead of blanking them. A live entry in ``currentWork`` always wins over a
 *  stale parked copy — restore only applies to cells that are being re-added
 *  (not present in ``currentWork``). */
export function rebuildCellWork(
  ids: string[],
  allProjects: { id: string; assigned_cell?: Team | "" }[],
  currentWork: CellWork[],
  priorByCell?: ReadonlyMap<Team, CellWork>,
): { the_work: CellWork[]; project_id: null } {
  const cellToPid = new Map<Team, string>();
  for (const id of ids) {
    const proj = allProjects.find((p) => p.id === id);
    const cell = proj?.assigned_cell;
    if (cell && !cellToPid.has(cell)) cellToPid.set(cell, id);
  }
  const covered = new Set<Team>();
  // Update existing cell entries in place, preserve summary/items.
  const updated = currentWork.map((w) => {
    const team = w?.team;
    if (team && cellToPid.has(team)) {
      covered.add(team);
      return { ...w, project_id: cellToPid.get(team)! };
    }
    return w;
  });
  // Append an entry for a newly-selected cell not already in the_work. If the
  // cell was toggled off and back on, restore its parked summary/items instead
  // of a blank entry — only when there's no live entry (covered) for it.
  const appended: CellWork[] = [];
  for (const [team, pid] of cellToPid) {
    if (!covered.has(team)) {
      const prior = priorByCell?.get(team);
      appended.push(
        prior
          ? { ...prior, project_id: pid }
          : { team, summary: "", items: [], project_id: pid },
      );
    }
  }
  // Drop entries for cells no longer selected.
  const the_work = [...updated, ...appended].filter((w) => {
    const team = w?.team;
    return team && cellToPid.has(team);
  });
  return { the_work, project_id: null };
}

/** Merge a draft's previously-parked cell content with its current ``the_work``
 *  into the next parked snapshot (F086). Previously-parked cells (toggled off,
 *  no longer in ``work``) are retained so a later re-select can restore them;
 *  live entries in ``work`` overwrite any stale parked copy, so an in-place edit
 *  is the content that gets parked. Pure + unit-tested; the hook's
 *  ``setBatchDraftProjects`` updater is a thin caller of this. */
export function parkCellWork(
  prevParked: readonly CellWork[],
  work: readonly CellWork[],
): CellWork[] {
  const byTeam = new Map<Team, CellWork>();
  for (const w of prevParked) {
    if (w?.team) byTeam.set(w.team, w);
  }
  for (const w of work) {
    if (w?.team) byTeam.set(w.team, w);
  }
  return Array.from(byTeam.values());
}

/** Fill each draft's missing project assignment from the MegaTask's scoped
 *  repos, so the review card is pre-filled instead of forcing the human to
 *  re-pick the projects they already scoped at intake. The intake prompt tells
 *  the agent to set a top-level ``project_id`` (each task lives in one repo),
 *  and the backend's scope validator falls back to it — so this mirrors that:
 *  an explicit per-cell ``the_work[].project_id`` wins; else the draft's
 *  top-level ``project_id`` (when scoped); else the single scoped repo that
 *  belongs to this cell, when unambiguous. An empty field stays empty (real
 *  ambiguity — 2+ scoped repos for the cell — the human picks). Returns the
 *  same batch reference when nothing changed (idempotent, no render loop). */
export function fillBatchProjects(
  batch: BatchProposal,
  projectIds: string[],
  allProjects: { id: string; assigned_cell?: Team | "" }[],
): BatchProposal {
  const scoped = new Set(projectIds);
  const scopedProjects = allProjects.filter((p) => scoped.has(p.id));
  const byCell = (team: Team) =>
    scopedProjects.filter((p) => p.assigned_cell === team);
  const isCell = (team: unknown): team is Team =>
    typeof team === "string" &&
    (CELL_TEAMS as readonly string[]).includes(team as Team);

  let changed = false;
  const drafts = batch.drafts.map((d) => {
    const work = Array.isArray(d.the_work) ? d.the_work : [];
    const cellWork = work.filter((w) => isCell(w?.team));

    // Draft with at least one the_work cell entry: fill each cell's project_id.
    if (cellWork.length > 0) {
      let workChanged = false;
      const newWork = work.map((w) => {
        if (
          !w ||
          !isCell(w.team) ||
          (w.project_id && scoped.has(w.project_id))
        ) {
          return w;
        }
        // (1) the draft's top-level project_id (the agent's main assignment),
        // but only when that repo actually belongs to this cell (a single-cell
        // draft's repo should match its one cell; a mismatch is an agent error
        // — fall through to auto-assign rather than land the wrong repo).
        const top = d.project_id;
        if (top && scoped.has(top)) {
          const proj = scopedProjects.find((p) => p.id === top);
          if (proj && proj.assigned_cell === w.team) {
            workChanged = true;
            return { ...w, project_id: top };
          }
        }
        // (2) exactly one scoped repo belongs to this cell.
        const matching = byCell(w.team);
        if (matching.length === 1) {
          workChanged = true;
          return { ...w, project_id: matching[0].id };
        }
        return w;
      });
      if (!workChanged) return d;
      changed = true;
      return { ...d, the_work: newWork };
    }

    // Legacy draft (no cell the_work): one top-level project_id. Fill it only
    // when exactly one scoped repo exists (truly unambiguous).
    if (d.project_id && scoped.has(d.project_id)) return d;
    if (scopedProjects.length === 1) {
      changed = true;
      return { ...d, project_id: scopedProjects[0].id };
    }
    return d;
  });

  return changed ? { ...batch, drafts } : batch;
}

// ---------------------------------------------------------------------------
// Refresh durability
//
// The chat lives entirely in React state, so a browser reload wiped it and
// dropped the human back to the scope form — even though the intake agent
// container outlives the page. Persist a small slice to localStorage (TTL'd)
// and, on mount, only restore it once the backend confirms the session is
// still alive. A full reload doesn't run React effect cleanup, so the
// navigate-away reap below never fires on refresh and the session survives.
// ---------------------------------------------------------------------------

const PERSIST_KEY = "roboco:prompter:live";
const PERSIST_TTL_MS = 30 * 60 * 1000; // 30 minutes

interface PersistedChat {
  sessionId: string;
  messages: ChatMessage[];
  state: PrompterState;
  scope: {
    targetKind: TargetKind;
    projectId: string;
    productId: string;
    projectIds?: string[];
  };
  editableDraft: EditableDraft;
  redraftTaskId?: string | null;
  // MegaTask review state, so a reload mid-batch-review restores the batch.
  batch?: BatchProposal | null;
  batchWaves?: number[][] | null;
  savedAt: number;
}

function loadPersisted(): PersistedChat | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(PERSIST_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as PersistedChat;
    if (!parsed.sessionId || Date.now() - parsed.savedAt > PERSIST_TTL_MS) {
      window.localStorage.removeItem(PERSIST_KEY);
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

function savePersisted(slice: PersistedChat): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(PERSIST_KEY, JSON.stringify(slice));
  } catch {
    // localStorage full / unavailable — durability is best-effort.
  }
}

function clearPersisted(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(PERSIST_KEY);
  } catch {
    // ignore
  }
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function usePrompter() {
  const [state, setState] = useState<PrompterState>("form");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [isSending, setIsSending] = useState(false);
  // Every project (carries assigned_cell), for pre-filling a MegaTask batch's
  // per-cell project_ids from the scoped repos (useProjects dedups with the
  // batch card's own query by key).
  const { data: allProjects = [] } = useProjects();
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
  // MegaTask scope: the set of (possibly unrelated) projects it spans.
  const [projectIds, setProjectIds] = useState<string[]>([]);
  const [initialMessage, setInitialMessage] = useState("");

  const [editableDraft, setEditableDraft] =
    useState<EditableDraft>(EMPTY_DRAFT);
  // The proposed MegaTask (when the agent calls propose_batch), its previewed
  // waves (computed without creating anything), and its create result.
  const [batch, setBatch] = useState<BatchProposal | null>(null);
  const [batchWaves, setBatchWaves] = useState<number[][] | null>(null);
  const [batchResult, setBatchResult] = useState<BatchConfirmResult | null>(
    null,
  );

  // Live-session plumbing held in refs so SSE callbacks never see stale state.
  const sessionIdRef = useRef<string | null>(null);
  // Set when this chat is a board-informed re-draft of an existing task: confirm
  // then updates that task in place instead of creating a new one.
  const redraftTaskIdRef = useRef<string | null>(null);
  const sourceRef = useRef<EventSource | null>(null);
  const streamingIdRef = useRef<string | null>(null);
  // Synchronous re-entry guard for launch — a double-click was creating two tasks.
  const launchingRef = useRef(false);
  const scopeRef = useRef({ targetKind, projectId, productId, projectIds });
  scopeRef.current = { targetKind, projectId, productId, projectIds };

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
          m.id === id ? { ...m, content: m.content + delta } : m,
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
      // Attach ONLY to the CURRENT turn's streaming message. Do NOT fall back to
      // "the last assistant message anywhere" — that can be a PRIOR turn's message
      // sitting above the user's latest message, which made the draft card render
      // above the user's "Yes, propose it". When there's no current streaming
      // message, append a fresh one so the card always lands at the bottom.
      const id = streamingIdRef.current;
      if (id) {
        return prev.map((m) =>
          m.id === id
            ? { ...m, draft, content: stripDraftFence(m.content) }
            : m,
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
            // Trailing prose AFTER a draft/batch tool call (the agent keeps
            // talking once the card is up) must still render in the bubble —
            // but it must NOT clobber the preview state, or the card vanishes
            // mid-turn and turn_end can't restore it (it only preserves these
            // states if they're still set). Keep the card up; the delta lands
            // in a fresh bubble (the batch/draft handler cleared streamingId).
            setState((s) =>
              s === "draft_preview" || s === "batch_preview" ? s : "streaming",
            );
          }
          break;
        case "tool_use":
          // A tool call ends the current text bubble, so the agent's words
          // before and after the tool render as separate messages (fixes
          // "two waves merged into one big bubble").
          streamingIdRef.current = null;
          setActivity(evt.tool ? `Using ${evt.tool}…` : "Working…");
          break;
        case "thinking":
          setActivity("Thinking…");
          break;
        case "turn_end":
          streamingIdRef.current = null;
          setActivity(null);
          setIsSending(false);
          setState((s) =>
            s === "draft_preview" || s === "batch_preview" ? s : "chatting",
          );
          break;
        case "draft": {
          const parsed = draftFromEvent(evt.data);
          if (parsed) {
            attachDraft(parsed.draft);
            setEditableDraft(
              toEditable(parsed.draft, parsed.scale, scopeRef.current),
            );
            setState("draft_preview");
          }
          break;
        }
        case "batch": {
          // A MegaTask: the agent proposed N drafts at once. Hold them for the
          // Review MegaTask card; the human confirms the whole batch together.
          const parsedBatch = batchFromEvent(evt.data);
          if (parsedBatch) {
            streamingIdRef.current = null;
            setBatch(parsedBatch);
            setBatchWaves(null);
            setState("batch_preview");
            if (parsedBatch.dropped > 0) {
              addMessage({
                role: "error",
                content:
                  `${parsedBatch.dropped} proposed task${
                    parsedBatch.dropped === 1 ? " was" : "s were"
                  } malformed and dropped from this MegaTask. Ask the agent to ` +
                  "re-propose them if they're needed.",
              });
            }
            // Compute the conflict-free waves (no task created) so the human can
            // review the sequencing before confirming. Best-effort.
            const sid = sessionIdRef.current;
            if (sid) {
              void prompterLiveApi
                .previewBatch(sid, parsedBatch.drafts)
                .then((p) => setBatchWaves(p.waves))
                .catch(() => setBatchWaves(null));
            }
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
    [appendDelta, attachDraft, addMessage],
  );

  const closeStream = useCallback(() => {
    sourceRef.current?.close();
    sourceRef.current = null;
  }, []);

  // A dropped connection or a session the server already tore down fires a
  // transport-level `error` event on the EventSource itself — a plain Event
  // with NO JSON payload. That is distinct from a server-sent `event: error`
  // frame (a MessageEvent carrying a LiveEvent). Without handling it the
  // transport error was swallowed by the JSON-parse try/catch in openStream
  // and `isSending` stayed true — the composer was permanently disabled and
  // the dead EventSource loop-reconnected a session that no longer existed.
  // Reset the turn, tell the user, and close the dead stream.
  const handleTransportError = useCallback(() => {
    streamingIdRef.current = null;
    setActivity(null);
    setIsSending(false);
    addMessage({
      role: "error",
      content:
        "Live connection lost — the chat session is no longer reachable. " +
        "Start a new chat to continue.",
    });
    // Keep a draft/batch preview up so the human can still act on a proposed
    // card; otherwise land on the stable chat state.
    setState((s) =>
      s === "draft_preview" || s === "batch_preview" ? s : "chatting",
    );
    closeStream();
  }, [addMessage, closeStream]);

  const openStream = useCallback(
    (sid: string) => {
      closeStream();
      const es = new EventSource(prompterLiveApi.streamUrl(sid));
      for (const kind of LIVE_EVENT_KINDS) {
        if (kind === "error") continue; // error is dual-purpose — handled below
        es.addEventListener(kind, (e: MessageEvent) => {
          try {
            handleEvent(JSON.parse(e.data) as LiveEvent);
          } catch {
            // A malformed frame is dropped; the stream stays open.
          }
        });
      }
      // `error` is dual-purpose. A server-sent `event: error` frame carries a
      // JSON LiveEvent (dispatched as a MessageEvent with string `data`) →
      // route it through handleEvent like any other kind. A transport-level
      // error (dropped connection / dead session) fires a plain Event with no
      // `data` → JSON.parse would swallow it and leave isSending stuck, so
      // route the no-payload case to the transport-error reset instead.
      es.addEventListener("error", (e: Event) => {
        const data = (e as MessageEvent).data;
        if (typeof data === "string") {
          try {
            handleEvent(JSON.parse(data) as LiveEvent);
          } catch {
            // A malformed server-sent error frame is dropped; stream stays open.
          }
        } else {
          handleTransportError();
        }
      });
      sourceRef.current = es;
    },
    [closeStream, handleEvent, handleTransportError],
  );

  // Best-effort reap if the user navigates away mid-chat. This cleanup runs on
  // SPA navigation (component unmount), NOT on a full page reload — so a reload
  // leaves the session running for the reconnect path below to pick up.
  useEffect(() => {
    return () => {
      closeStream();
      const sid = sessionIdRef.current;
      if (sid) {
        void prompterLiveApi.stop(sid).catch(() => undefined);
        clearPersisted();
      }
    };
  }, [closeStream]);

  // Persist the live chat whenever it changes, so a reload can resume it.
  useEffect(() => {
    const persistable =
      sessionId !== null &&
      (state === "chatting" ||
        state === "streaming" ||
        state === "draft_preview" ||
        state === "batch_preview" ||
        state === "review_modal");
    if (persistable && sessionId) {
      savePersisted({
        sessionId,
        messages,
        state,
        scope: scopeRef.current,
        editableDraft,
        redraftTaskId: redraftTaskIdRef.current,
        batch,
        batchWaves,
        savedAt: Date.now(),
      });
    }
  }, [sessionId, messages, state, editableDraft, batch, batchWaves]);

  // Pre-fill each MegaTask draft's project from the scoped repos (the agent
  // sets a top-level project_id; the backend falls back to it). Without this
  // the review card shows empty Selects and blocks launch, forcing the human
  // to re-pick the very projects they scoped at intake. Idempotent: once every
  // cell has a project_id it returns the same batch reference (no loop).
  useEffect(() => {
    if (!batch) return;
    const filled = fillBatchProjects(batch, projectIds, allProjects);
    if (filled !== batch) setBatch(filled);
  }, [batch, projectIds, allProjects]);

  // On mount, reconnect to a still-running session left behind by a reload.
  const didRestoreRef = useRef(false);
  useEffect(() => {
    if (didRestoreRef.current) return;
    didRestoreRef.current = true;
    const persisted = loadPersisted();
    if (!persisted) return;
    let cancelled = false;
    void (async () => {
      try {
        const { alive } = await prompterLiveApi.status(persisted.sessionId);
        if (cancelled) return;
        if (!alive) {
          clearPersisted();
          return;
        }
        // Restore the history + scope + draft, then reopen the stream for new
        // events. Any tokens from a turn that was mid-flight at reload are gone,
        // so land in a stable state rather than "streaming".
        sessionIdRef.current = persisted.sessionId;
        redraftTaskIdRef.current = persisted.redraftTaskId ?? null;
        setSessionId(persisted.sessionId);
        setMessages(persisted.messages);
        setEditableDraft(persisted.editableDraft);
        setTargetKind(persisted.scope.targetKind);
        setProjectId(persisted.scope.projectId);
        setProductId(persisted.scope.productId);
        setProjectIds(persisted.scope.projectIds ?? []);
        setBatch(persisted.batch ?? null);
        setBatchWaves(persisted.batchWaves ?? null);
        // A MegaTask review survives reload; otherwise land on a stable state.
        setState(
          persisted.state === "batch_preview" && persisted.batch
            ? "batch_preview"
            : persisted.state === "draft_preview" ||
                persisted.state === "review_modal"
              ? "draft_preview"
              : "chatting",
        );
        openStream(persisted.sessionId);
      } catch {
        // Status check failed (server unreachable) — stay on the form and keep
        // the persisted slice for a later retry within its TTL.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [openStream]);

  // -----------------------------------------------------------------------
  // Start the live session (from the scope form)
  // -----------------------------------------------------------------------

  const isFormValid = useCallback((): boolean => {
    const scoped =
      targetKind === "product"
        ? productId !== ""
        : targetKind === "megatask"
          ? projectIds.length >= 2 // a MegaTask spans several repos
          : projectId !== "";
    return scoped && initialMessage.trim().length > 0;
  }, [targetKind, projectId, productId, projectIds, initialMessage]);

  const start = useCallback(async () => {
    if (!isFormValid() || state === "preparing") return;
    const opening = initialMessage.trim();
    setState("preparing");
    addMessage({ role: "user", content: opening });
    const scopePayload =
      targetKind === "product"
        ? { product_id: productId }
        : targetKind === "megatask"
          ? { project_ids: projectIds }
          : { project_id: projectId };
    try {
      const { session_id } = await prompterLiveApi.start({
        ...scopePayload,
        initial_message: opening,
      });
      sessionIdRef.current = session_id;
      setSessionId(session_id);
      openStream(session_id);
      setIsSending(true); // the opening reply is on its way over SSE
      // start now returns immediately; the container spawns in the background
      // (clone + image build can take a minute). Show that until the first event.
      setActivity(
        "Preparing the agent — cloning your repo and reading the code…",
      );
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
    projectIds,
    addMessage,
    openStream,
  ]);

  // -----------------------------------------------------------------------
  // Re-draft an existing board-reviewed task with the board's feedback
  // -----------------------------------------------------------------------

  const startRedraft = useCallback(
    async (taskId: string) => {
      if (state === "preparing" || sessionIdRef.current) return;
      setState("preparing");
      try {
        // Scope the chat to the task's product/project so launch has a target
        // even if the re-drafted proposal omits it.
        const task = await tasksApi.get(taskId);
        if (task.product_id) {
          setTargetKind("product");
          setProductId(task.product_id);
        } else if (task.project_id) {
          setTargetKind("project");
          setProjectId(task.project_id);
        }
        const { session_id } = await prompterLiveApi.reInterview(taskId);
        redraftTaskIdRef.current = taskId;
        sessionIdRef.current = session_id;
        setSessionId(session_id);
        openStream(session_id);
        setIsSending(true);
        setActivity("Re-opening intake with the board's feedback…");
        setState("streaming");
      } catch (err) {
        addMessage({ role: "error", content: getErrorMessage(err) });
        setState("form");
      }
    },
    [state, addMessage, openStream],
  );

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
    [isSending, addMessage],
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
      (editableDraft.description ?? "").trim().length >= 20 &&
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

  const launchTask = useCallback(
    async (route: StartRoute) => {
      // Re-entry guard FIRST (synchronous, no stale closure): a double-click was
      // firing two confirms and creating duplicate tasks.
      if (launchingRef.current) return;
      const sid = sessionIdRef.current;
      // Never fail silently — a dead button with no feedback reads as "broken"
      // (it did: a missing `description` threw inside validation and the click
      // vanished). Tell the human exactly what's blocking the launch.
      if (!sid) {
        toast.error("This chat has ended — start a new one to launch a task.");
        return;
      }
      if (!isValidForLaunch()) {
        toast.error(
          "The draft is missing something needed to launch: a title, a 20+ character " +
            "summary, at least one acceptance criterion, and a target. Keep chatting to refine it.",
        );
        return;
      }

      launchingRef.current = true;
      setIsLaunching(true);
      setState("launching");

      const draft: DraftProposal = {
        title: editableDraft.title.trim(),
        description: (editableDraft.description ?? "").trim(),
        acceptance_criteria: editableDraft.acceptance_criteria,
        team: editableDraft.team as Team,
        priority: editableDraft.priority,
        objective: editableDraft.objective.trim() || null,
        what_this_builds: editableDraft.what_this_builds,
        the_work: editableDraft.the_work,
        notes: editableDraft.notes,
        ...(editableDraft.task_type
          ? { task_type: editableDraft.task_type }
          : {}),
        ...(editableDraft.nature ? { nature: editableDraft.nature } : {}),
        ...(editableDraft.estimated_complexity
          ? { estimated_complexity: editableDraft.estimated_complexity }
          : {}),
      };

      const payload: ConfirmPayload =
        editableDraft.targetKind === "product"
          ? { product_id: editableDraft.productId, draft, route }
          : { project_id: editableDraft.projectId, draft, route };
      // Board-informed re-draft: confirm updates the existing task in place.
      const redraftId = redraftTaskIdRef.current;
      if (redraftId) {
        payload.task_id = redraftId;
      }

      const effectiveTeam =
        editableDraft.targetKind === "product"
          ? Team.MAIN_PM
          : (editableDraft.team as Team);

      try {
        const { task_id } = await prompterLiveApi.confirm(sid, payload);
        // Board route, first pass: the backend parked the intake agent so the
        // board's feedback can be injected for an in-place re-draft. Keep the chat
        // alive (don't reap) — the revised draft will arrive here to approve.
        if (route === "board" && !redraftId) {
          redraftTaskIdRef.current = task_id;
          addMessage({
            role: "assistant",
            content:
              "Sent to the board — the Product Owner and Head of Marketing are " +
              "reviewing this. Their feedback will arrive here as a revised draft " +
              "you can approve. You can leave and come back; this chat stays open.",
          });
          setState("chatting");
          return; // `finally` resets the launching guard
        }
        // The draft became a task — reap the agent and close the stream.
        closeStream();
        void prompterLiveApi.stop(sid).catch(() => undefined);
        clearPersisted();
        sessionIdRef.current = null;
        redraftTaskIdRef.current = null;
        setCreatedTaskId(task_id);
        setCreatedTaskTitle(draft.title);
        setCreatedTaskTeam(effectiveTeam);
        toast.success("Task created and launched!");
        setState("success");
      } catch (err) {
        toast.error(`Failed to launch task: ${getErrorMessage(err)}`);
        setState("draft_preview"); // back to the draft card to retry
      } finally {
        setIsLaunching(false);
        launchingRef.current = false;
      }
    },
    [editableDraft, isValidForLaunch, closeStream, addMessage],
  );

  // -----------------------------------------------------------------------
  // Confirm a MegaTask — create the umbrella + sequenced root-subtasks, reap
  // -----------------------------------------------------------------------

  /** Set the projects one MegaTask task targets. The review card exposes a
   *  multi-select checkbox list per task (one task can span several repos), so
   *  the human picks the whole set at once instead of one dropdown per cell.
   *  A RoboCo project is per-cell, so the selection maps to one project per
   *  cell in ``the_work[]`` (the backend's ``task_cell_projects`` is unique per
   *  ``(task, team)`` — one repo per cell). Existing entries keep their
   *  summary/items; a newly-selected cell gets a minimal entry; a deselected
   *  cell's entry is dropped (that cell no longer participates). The top-level
   *  ``project_id`` is cleared — a multi-repo task targets via its cell map.
   *  Project choice does not affect the wave plan (waves derive from collision
   *  surface), so the previewed waves stay valid. */
  const setBatchDraftProjects = useCallback(
    (index: number, ids: string[]) => {
      setBatch((prev) => {
        if (!prev) return prev;
        const draft = prev.drafts[index];
        const work: CellWork[] = Array.isArray(draft?.the_work)
          ? (draft!.the_work as CellWork[])
          : [];
        // Park each cell's last-seen content so a later re-select of a
        // toggled-off cell restores its summary/items instead of blanking
        // them (F086). Previously-parked cells seed the map, then the live
        // entries win (so an in-place edit is the copy that gets parked).
        const parkedSnapshot = parkCellWork(
          prev.parkedCellWork?.[index] ?? [],
          work,
        );
        const prior = new Map<Team, CellWork>(
          parkedSnapshot
            .filter((w): w is CellWork => !!w?.team)
            .map((w) => [w.team, w]),
        );
        return {
          ...prev,
          drafts: prev.drafts.map((d, i) =>
            i === index
              ? {
                  ...d,
                  the_work: rebuildCellWork(ids, allProjects, work, prior)
                    .the_work,
                  project_id: null,
                }
              : d,
          ),
          // Persist the parked snapshot (every cell's latest content, selected
          // or not) so the next toggle can restore from it.
          parkedCellWork: {
            ...(prev.parkedCellWork ?? {}),
            [index]: parkedSnapshot,
          },
        };
      });
    },
    [allProjects],
  );

  const confirmBatch = useCallback(
    async (route: StartRoute) => {
      if (launchingRef.current) return;
      const sid = sessionIdRef.current;
      if (!sid) {
        toast.error(
          "This chat has ended — start a new one to launch a MegaTask.",
        );
        return;
      }
      if (!batch || batch.drafts.length === 0) {
        toast.error(
          "No MegaTask to launch yet — keep chatting to propose one.",
        );
        return;
      }
      // Every cell of every task must target one of the scoped repos (the agent
      // assigns it, the human can fix it). A multi-cell draft carries its
      // per-cell project_ids in the_work[]; a legacy single-cell draft falls back
      // to its top-level project_id. The backend re-asserts this authoritatively.
      const scoped = scopeRef.current.projectIds;
      const scopedSet = new Set(scoped);
      for (let i = 0; i < batch.drafts.length; i++) {
        const d = batch.drafts[i];
        const pids = draftCellProjectIds(d);
        const targets =
          pids.length > 0 ? pids : d.project_id ? [d.project_id] : [];
        if (targets.length === 0) {
          toast.error(
            `Task ${i + 1} ("${d.title}") has no project. ` +
              "Pick one for each of its cells in the review card.",
          );
          return;
        }
        const bad = targets.find((pid) => !scopedSet.has(pid));
        if (bad !== undefined) {
          toast.error(
            `Task ${i + 1} ("${d.title}") targets a project outside this ` +
              "MegaTask's selected repos. Pick it in the review card.",
          );
          return;
        }
      }

      launchingRef.current = true;
      setIsLaunching(true);
      setState("launching");
      try {
        const result = await prompterLiveApi.confirmBatch(sid, {
          title: batch.title.trim() || "MegaTask",
          drafts: batch.drafts,
          project_ids: scopeRef.current.projectIds,
          route,
        });
        closeStream();
        void prompterLiveApi.stop(sid).catch(() => undefined);
        clearPersisted();
        sessionIdRef.current = null;
        setBatchResult(result);
        setCreatedTaskId(result.umbrella_task_id);
        setCreatedTaskTitle(batch.title.trim() || "MegaTask");
        setCreatedTaskTeam(route === "board" ? Team.BOARD : Team.MAIN_PM);
        if (route === "board") {
          // Board route: the umbrella + root-subtasks are created HELD for the
          // PO + HoM to review — nothing is dispatched yet. The CEO releases the
          // sequenced tasks with Approve & Start on the umbrella task once the
          // board finishes (the existing CEO gate, not this chat). Say so, so
          // "Board review & Start" doesn't read as "launched" the way the
          // Main-PM route does.
          toast.success(
            "Sent to the Board for review — the Product Owner and Head of " +
              "Marketing will review this MegaTask. Approve & Start it from the " +
              "umbrella task once they're done.",
          );
        } else {
          toast.success(
            `MegaTask launched — ${result.root_subtask_ids.length} tasks in ` +
              `${result.waves.length} wave${result.waves.length === 1 ? "" : "s"}.`,
          );
        }
        setState("success");
      } catch (err) {
        toast.error(`Failed to launch MegaTask: ${getErrorMessage(err)}`);
        setState("batch_preview");
      } finally {
        setIsLaunching(false);
        launchingRef.current = false;
      }
    },
    [batch, closeStream],
  );

  // -----------------------------------------------------------------------
  // Reset to start another conversation
  // -----------------------------------------------------------------------

  const startAnother = useCallback(() => {
    closeStream();
    const sid = sessionIdRef.current;
    if (sid) void prompterLiveApi.stop(sid).catch(() => undefined);
    clearPersisted();
    sessionIdRef.current = null;
    streamingIdRef.current = null;
    setMessages([]);
    setSessionId(null);
    setActivity(null);
    setEditableDraft(EMPTY_DRAFT);
    setBatch(null);
    setBatchWaves(null);
    setBatchResult(null);
    setProjectId("");
    setProductId("");
    setProjectIds([]);
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
    projectIds,
    setProjectIds,
    initialMessage,
    setInitialMessage,
    isFormValid,
    start,
    startRedraft,

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

    // MegaTask
    batch,
    batchWaves,
    batchResult,
    setBatchDraftProjects,
    confirmBatch,
  };
}
