"use client";

/**
 * Chat — the phone twin of the desktop A2A Switchboard, split honestly
 * along the two things it actually contains:
 *
 *   Mine  — the CEO's own 1:1 threads (participant-scoped route: resolved
 *           peer, real unread counts, mark-read on open, plain send).
 *   Fleet — watched agent↔agent conversations (admin route). The CEO can
 *           interject only when the thread is task-linked (the reply route's
 *           own contract); otherwise the thread is watch-only and says so.
 *
 * Agent messages render as markdown (they're written in it), bubbles carry
 * Telegram-style times, rows flash on live WS frames, and the thread poll
 * only runs while the socket is down.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  a2aLiveKeys,
  useA2AConversations,
  useA2AMessages,
  useCeoConversations,
  useCreateCeoConversation,
  useMarkConversationRead,
  useReplyAsCeo,
  useSendCeoMessage,
} from "@/hooks/use-a2a-live";
import { useA2ALiveStream } from "@/hooks/use-websocket";
import { useSecretary, type ChatMessage } from "@/hooks/use-secretary";
import { secretaryApi } from "@/lib/api/secretary";
import { CEO_SLUG } from "@/components/a2a/a2a-utils";
import { AgentSelector } from "@/components/agents/agent-selector";
import { EXCLUDE_NON_DM_ROLES } from "@/components/a2a/a2a-new-dm-dialog";
import { getAgentDisplayName } from "@/lib/agent-utils";
import { getErrorMessage } from "@/lib/api/client";
import type {
  A2AChatMessage,
  AdminConversationSummary,
  CeoConversationSummary,
} from "@/lib/api/a2a";
import { Markdown } from "@/components/ui/markdown";
import { Textarea } from "@/components/ui/textarea";
import {
  TgAvatar,
  TgSegmented,
  TgSubPage,
  TG_CARD,
  TG_PRESS,
} from "@/components/tg/ui";
import { cleanPreview, useTaskNameIndex } from "@/components/tg/tg-format";
import { isTgDemoMode } from "@/lib/telegram/demo";
import {
  DEMO_CHAT_FLEET,
  DEMO_CHAT_MESSAGES,
  DEMO_CHAT_MINE,
} from "@/lib/telegram/demo-data";
import { haptics } from "@/lib/telegram/webapp";
import {
  ArrowDown,
  CaretRight,
  CircleNotch,
  Eye,
  NotePencil,
  PaperPlaneTilt,
} from "@phosphor-icons/react";
import { format, isSameDay, isToday, isYesterday } from "date-fns";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

/** Fallback cadence for the actively-viewed thread when the /ws/system
 * socket is down — live frames drive refresh whenever it's connected. */
const THREAD_POLL_MS = 10_000;

type ThreadRef =
  | { kind: "mine"; id: string; peer: string }
  | { kind: "fleet"; id: string; a: string; b: string; taskId: string | null };

type ChatView =
  | { mode: "list" }
  | { mode: "compose" }
  | { mode: "secretary" }
  | { mode: "thread"; ref: ThreadRef };

/** Telegram-style compact age: now · 5m · 3h · 2d · Mar 4. */
const shortTime = (iso: string) => {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "now";
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86_400) return `${Math.floor(s / 3600)}h`;
  if (s < 604_800) return `${Math.floor(s / 86_400)}d`;
  return format(new Date(iso), "MMM d");
};

function DayDivider({ date }: { date: Date }) {
  const label = isToday(date)
    ? "Today"
    : isYesterday(date)
      ? "Yesterday"
      : format(date, "MMM d");
  return (
    <div className="flex justify-center py-1">
      <span className="rounded-full bg-muted/60 px-2.5 py-0.5 text-[11px] font-medium text-muted-foreground">
        {label}
      </span>
    </div>
  );
}

function UnreadBadge({ count }: { count: number }) {
  if (count <= 0) return null;
  return (
    <span className="flex h-5 min-w-5 items-center justify-center rounded-full bg-primary px-1.5 text-[11px] font-semibold text-primary-foreground">
      {count > 99 ? "99+" : count}
    </span>
  );
}

function PairAvatars({ a, b }: { a: string; b: string }) {
  return (
    <span className="flex shrink-0 -space-x-2">
      <TgAvatar name={a} size="sm" />
      <TgAvatar name={b} size="sm" />
    </span>
  );
}

// ---------------------------------------------------------------------------
// Secretary — the chief-of-staff live chat (same session runtime the desktop
// panel drives), pinned above the CEO's A2A threads. This is the "do work
// from the phone" surface: directives, company questions, gated actions.
// ---------------------------------------------------------------------------

const DEMO_SECRETARY: ChatMessage[] = [
  { role: "user", text: "What shipped this week, one line each?" },
  {
    role: "assistant",
    text: "This week:\n\n- **v0.26.0** released (video preview frames, X caption fix)\n- Env-branch DM wording parity fixes merged\n- Docs sweep for the release published to docs.roboco.tech",
  },
];

function SecretaryPinnedRow({ onOpen }: { onOpen: () => void }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className={cn(
        TG_CARD,
        "flex w-full items-center gap-3 px-3.5 py-3 text-left",
        TG_PRESS,
      )}
    >
      <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-b from-primary to-primary/80 text-[10px] font-semibold text-primary-foreground">
        SEC
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-[15px] font-medium">Secretary</p>
        <p className="truncate text-[13px] text-muted-foreground">
          Directives, questions, company state
        </p>
      </div>
      <CaretRight
        weight="bold"
        className="h-4 w-4 shrink-0 text-muted-foreground/40"
      />
    </button>
  );
}

// Mirrors useSecretary's own localStorage key (panel/src/hooks/use-secretary.ts,
// PERSIST_KEY) — read-only here, just to tell "this device has nothing of
// its own to restore" apart from "a restore is still resolving" before this
// view decides whether it's safe to auto-start.
const SECRETARY_PERSIST_KEY = "roboco:secretary:live";

function hasPersistedSecretarySession(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(SECRETARY_PERSIST_KEY) !== null;
  } catch {
    return false;
  }
}

function SecretaryView({ onBack }: { onBack: () => void }) {
  const demo = isTgDemoMode();
  const { sessionId, messages, streaming, start, send, stop } = useSecretary();
  const shown = demo ? DEMO_SECRETARY : messages;

  // The Secretary is a backend singleton (one container at a time) — a
  // device with nothing of its own to restore auto-starting anyway would
  // silently preempt a live session on another device. So this view only
  // auto-starts unconditionally when it has a session of its own to resume
  // (unchanged from before); a genuinely fresh device checks the singleton
  // first and offers an explicit take-over instead of blind-starting.
  const [activeElsewhere, setActiveElsewhere] = useState(false);
  const startedRef = useRef(false);
  useEffect(() => {
    if (demo || startedRef.current || sessionId) return;
    if (hasPersistedSecretarySession()) {
      startedRef.current = true;
      start().catch((err) => toast.error(getErrorMessage(err)));
      return;
    }
    let cancelled = false;
    const goAhead = () => {
      if (cancelled || startedRef.current) return;
      startedRef.current = true;
      void start().catch((err) => toast.error(getErrorMessage(err)));
    };
    secretaryApi
      .isActive()
      .then((active) => {
        if (cancelled) return;
        if (active) setActiveElsewhere(true);
        else goAhead();
      })
      // A failed status check shouldn't strand a fresh device with neither
      // a chat nor a takeover button — fall back to the prior behavior.
      .catch(goAhead);
    return () => {
      cancelled = true;
    };
  }, [demo, sessionId, start]);

  const takeOver = () => {
    haptics.tap();
    setActiveElsewhere(false);
    startedRef.current = true;
    void start().catch((err) => toast.error(getErrorMessage(err)));
  };

  const scrollRef = useRef<HTMLDivElement>(null);
  const count = shown.length;
  useEffect(() => {
    const raf = requestAnimationFrame(() => {
      const el = scrollRef.current;
      if (el) el.scrollTop = el.scrollHeight;
    });
    return () => cancelAnimationFrame(raf);
  }, [count, streaming]);

  return (
    <TgSubPage
      title="Secretary"
      subtitle="Chief of staff"
      onBack={onBack}
      trailing={
        !demo && sessionId ? (
          <button
            type="button"
            onClick={() => {
              haptics.tap();
              void stop().then(onBack);
            }}
            className="rounded-full bg-muted/60 px-3 py-2 text-xs font-medium text-muted-foreground"
          >
            End
          </button>
        ) : undefined
      }
    >
      {activeElsewhere && !demo && !sessionId ? (
        <div className="flex flex-col items-center gap-3 py-10 text-center">
          <p className="text-sm text-muted-foreground">
            A Secretary session is live on another device.
          </p>
          <button
            type="button"
            onClick={takeOver}
            className={cn(
              "rounded-full bg-primary px-4 py-2 text-[15px] font-semibold text-primary-foreground",
              TG_PRESS,
            )}
          >
            Take over
          </button>
        </div>
      ) : (
        <>
          <div
            ref={scrollRef}
            className="max-h-[58dvh] space-y-1.5 overflow-y-auto pb-1"
          >
            {shown.length === 0 && (
              <p className="py-10 text-center text-sm text-muted-foreground">
                {demo || sessionId
                  ? "Ask anything: company state, queues, directives."
                  : "Waking the Secretary…"}
              </p>
            )}
            {shown.map((m, i) => (
              <div
                key={i}
                className={cn(
                  "flex",
                  m.role === "user" ? "justify-end" : "justify-start",
                )}
              >
                <div
                  className={cn(
                    "max-w-[82%] rounded-2xl px-3.5 py-2 text-[15px] leading-relaxed",
                    m.role === "user"
                      ? "rounded-br-md bg-primary text-primary-foreground"
                      : "rounded-bl-md bg-card",
                  )}
                >
                  {m.role === "user" ? (
                    <p className="whitespace-pre-wrap break-words">
                      {m.text}
                    </p>
                  ) : (
                    <Markdown
                      compact
                      className="prose prose-sm prose-invert max-w-none [&_p]:my-1 first:[&_p]:mt-0 last:[&_p]:mb-0"
                    >
                      {m.text || "…"}
                    </Markdown>
                  )}
                </div>
              </div>
            ))}
            {streaming && shown[shown.length - 1]?.role === "user" && (
              <div className="flex justify-start">
                <div className="rounded-2xl rounded-bl-md bg-card px-3.5 py-2.5">
                  <span className="inline-flex gap-1">
                    {[0, 1, 2].map((d) => (
                      <span
                        key={d}
                        className="h-1.5 w-1.5 animate-pulse rounded-full bg-muted-foreground/60"
                        style={{ animationDelay: `${d * 150}ms` }}
                      />
                    ))}
                  </span>
                </div>
              </div>
            )}
          </div>
          <Composer
            placeholder={demo ? "Demo mode, sends disabled" : "Message…"}
            pending={streaming}
            disabled={demo || (!demo && !sessionId)}
            onSend={(text) => {
              void send(text).catch((err) => toast.error(getErrorMessage(err)));
            }}
          />
        </>
      )}
    </TgSubPage>
  );
}

// ---------------------------------------------------------------------------
// Conversation list
// ---------------------------------------------------------------------------

function MineList({
  pulses,
  onOpen,
}: {
  pulses: Record<string, number>;
  onOpen: (ref: ThreadRef) => void;
}) {
  const demo = isTgDemoMode();
  const { data, isLoading, isError, refetch } = useCeoConversations(50, !demo);
  const resolveTask = useTaskNameIndex();
  const items: CeoConversationSummary[] = demo
    ? DEMO_CHAT_MINE
    : (data?.items ?? []);

  if (isLoading && !demo) return <ListSkeleton />;
  if (isError && !demo) return <ListError onRetry={() => void refetch()} />;
  if (!items.length) {
    return (
      <p className="py-10 text-center text-sm text-muted-foreground">
        No direct chats yet. Start one with the compose button.
      </p>
    );
  }
  return (
    <div className={cn(TG_CARD, "divide-y divide-white/[0.05] px-2 py-1")}>
      {items.map((c) => (
        <button
          key={`${c.id}-${pulses[c.id] ?? 0}`}
          type="button"
          onClick={() =>
            onOpen({
              kind: "mine",
              id: c.id,
              peer: c.other_agent,
            })
          }
          className={cn(
            "flex min-h-14 w-full items-center gap-3 rounded-xl px-1.5 py-2.5 text-left transition-colors duration-200 active:bg-white/[0.05]",
            pulses[c.id] && "tg-flash",
          )}
        >
          <TgAvatar name={c.other_agent} />
          <div className="min-w-0 flex-1">
            <div className="flex items-baseline justify-between gap-2">
              <span className="min-w-0 truncate text-[15px] font-medium">
                {getAgentDisplayName(c.other_agent)}
              </span>
              {c.last_message_at && (
                <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground/70">
                  {shortTime(c.last_message_at)}
                </span>
              )}
            </div>
            <div className="mt-0.5 flex items-center justify-between gap-2">
              <p
                className={cn(
                  "min-w-0 truncate text-[13px] leading-snug",
                  c.unread_count > 0
                    ? "font-medium text-foreground/80"
                    : "text-muted-foreground",
                )}
              >
                {c.last_message_preview
                  ? cleanPreview(c.last_message_preview, resolveTask)
                  : "No messages yet"}
              </p>
              <UnreadBadge count={c.unread_count} />
            </div>
          </div>
        </button>
      ))}
    </div>
  );
}

function FleetList({
  pulses,
  onOpen,
}: {
  pulses: Record<string, number>;
  onOpen: (ref: ThreadRef) => void;
}) {
  const demo = isTgDemoMode();
  const { data, isLoading, isError, refetch } = useA2AConversations(50, !demo);
  const resolveTask = useTaskNameIndex();
  const items: AdminConversationSummary[] = (
    demo ? DEMO_CHAT_FLEET : (data?.items ?? [])
  ).filter((c) => c.agent_a !== CEO_SLUG && c.agent_b !== CEO_SLUG);

  if (isLoading && !demo) return <ListSkeleton />;
  if (isError && !demo) return <ListError onRetry={() => void refetch()} />;
  if (!items.length) {
    return (
      <p className="py-10 text-center text-sm text-muted-foreground">
        No agent conversations yet.
      </p>
    );
  }
  return (
    <div className={cn(TG_CARD, "divide-y divide-white/[0.05] px-2 py-1")}>
      {items.map((c) => {
        const taskName = c.task_id ? resolveTask(c.task_id) : undefined;
        return (
          <button
            key={`${c.id}-${pulses[c.id] ?? 0}`}
            type="button"
            onClick={() =>
              onOpen({
                kind: "fleet",
                id: c.id,
                a: c.agent_a,
                b: c.agent_b,
                taskId: c.task_id,
              })
            }
            className={cn(
              "flex min-h-14 w-full items-center gap-3 rounded-xl px-1.5 py-2.5 text-left transition-colors duration-200 active:bg-white/[0.05]",
              pulses[c.id] && "tg-flash",
            )}
          >
            <PairAvatars a={c.agent_a} b={c.agent_b} />
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline justify-between gap-2">
                <span className="min-w-0 truncate text-[15px] font-medium">
                  {getAgentDisplayName(c.agent_a)}
                  <span className="mx-1 text-muted-foreground/50">↔</span>
                  {getAgentDisplayName(c.agent_b)}
                </span>
                {c.last_message_at && (
                  <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground/70">
                    {shortTime(c.last_message_at)}
                  </span>
                )}
              </div>
              <p className="mt-0.5 truncate text-[13px] leading-snug text-muted-foreground">
                {c.topic ??
                  (c.last_message_preview
                    ? cleanPreview(c.last_message_preview, resolveTask)
                    : "No messages yet")}
                {taskName && (
                  <span className="text-muted-foreground/60">
                    {" "}
                    · {taskName}
                  </span>
                )}
              </p>
            </div>
          </button>
        );
      })}
    </div>
  );
}

function ListSkeleton() {
  return (
    <div className="space-y-2">
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="h-16 animate-pulse rounded-[20px] bg-card" />
      ))}
    </div>
  );
}

function ListError({ onRetry }: { onRetry: () => void }) {
  return (
    <div className={cn(TG_CARD, "p-4 text-center")}>
      <p className="text-sm text-muted-foreground">
        Couldn&apos;t load conversations.
      </p>
      <button
        type="button"
        onClick={onRetry}
        className={cn(
          "mt-2 rounded-full bg-muted px-4 py-1.5 text-sm font-medium",
          TG_PRESS,
        )}
      >
        Retry
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Thread
// ---------------------------------------------------------------------------

/** Bubble timeline for the CEO's own 1:1 — CEO right in accent, agent left
 * on card surface, times shown on the last message of each sender run. */
function MineTimeline({ items }: { items: A2AChatMessage[] }) {
  return (
    <>
      {items.map((m, i) => {
        const prev = items[i - 1];
        const next = items[i + 1];
        const mine = m.from_agent === CEO_SLUG;
        const endOfRun =
          !next ||
          next.from_agent !== m.from_agent ||
          !isSameDay(new Date(next.created_at), new Date(m.created_at));
        return (
          <div key={m.id}>
            {(!prev ||
              !isSameDay(
                new Date(prev.created_at),
                new Date(m.created_at),
              )) && <DayDivider date={new Date(m.created_at)} />}
            <div className={cn("flex", mine ? "justify-end" : "justify-start")}>
              <div
                className={cn(
                  "max-w-[82%] rounded-2xl px-3.5 py-2 text-[15px] leading-relaxed",
                  mine
                    ? "rounded-br-md bg-primary text-primary-foreground"
                    : "rounded-bl-md bg-card",
                )}
              >
                {mine ? (
                  <p className="whitespace-pre-wrap break-words">{m.content}</p>
                ) : (
                  <Markdown
                    compact
                    className="prose prose-sm prose-invert max-w-none [&_p]:my-1 first:[&_p]:mt-0 last:[&_p]:mb-0"
                  >
                    {m.content}
                  </Markdown>
                )}
              </div>
            </div>
            {endOfRun && (
              <p
                className={cn(
                  "mt-0.5 px-1 text-[10px] tabular-nums text-muted-foreground/60",
                  mine ? "text-right" : "text-left",
                )}
              >
                {format(new Date(m.created_at), "HH:mm")}
              </p>
            )}
          </div>
        );
      })}
    </>
  );
}

/** Transcript rows for a watched agent↔agent thread — the desktop model:
 * explicit identity per message (avatar + name), never side-inferred. */
function FleetTimeline({ items }: { items: A2AChatMessage[] }) {
  return (
    <>
      {items.map((m, i) => {
        const prev = items[i - 1];
        return (
          <div key={m.id}>
            {(!prev ||
              !isSameDay(
                new Date(prev.created_at),
                new Date(m.created_at),
              )) && <DayDivider date={new Date(m.created_at)} />}
            <div className={cn(TG_CARD, "rounded-2xl px-3.5 py-2.5")}>
              <div className="mb-1 flex items-center gap-2">
                <TgAvatar name={m.from_agent} size="sm" />
                <span className="text-[13px] font-semibold">
                  {getAgentDisplayName(m.from_agent)}
                </span>
                <span className="ml-auto text-[10px] tabular-nums text-muted-foreground/60">
                  {format(new Date(m.created_at), "HH:mm")}
                </span>
              </div>
              <Markdown
                compact
                className="prose prose-sm prose-invert max-w-none text-[14px] [&_p]:my-1 first:[&_p]:mt-0 last:[&_p]:mb-0"
              >
                {m.content}
              </Markdown>
            </div>
          </div>
        );
      })}
    </>
  );
}

function Composer({
  placeholder,
  pending,
  disabled = false,
  onSend,
}: {
  placeholder: string;
  /** An in-flight send — spinner on the send button. */
  pending: boolean;
  /** No sending at all (demo mode, session not up) — no spinner. */
  disabled?: boolean;
  onSend: (text: string) => void;
}) {
  const [draft, setDraft] = useState("");
  const submit = () => {
    const trimmed = draft.trim();
    if (!trimmed || pending || disabled) return;
    haptics.tap();
    onSend(trimmed);
    setDraft("");
  };
  return (
    <div className="flex items-end gap-2 pt-2">
      <div className="flex-1 rounded-3xl bg-card px-1.5 py-1 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]">
        <Textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder={placeholder}
          rows={1}
          className="max-h-32 min-h-[38px] resize-none border-0 bg-transparent px-2.5 py-1.5 text-[15px] shadow-none focus-visible:ring-0"
          disabled={pending || disabled}
        />
      </div>
      <button
        type="button"
        aria-label="Send"
        disabled={!draft.trim() || pending || disabled}
        onClick={submit}
        className={cn(
          "flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground disabled:opacity-40",
          TG_PRESS,
        )}
      >
        {pending ? (
          <CircleNotch weight="bold" className="h-4 w-4 animate-spin" />
        ) : (
          <PaperPlaneTilt weight="fill" className="h-4 w-4" />
        )}
      </button>
    </div>
  );
}

function ThreadView({
  threadRef,
  live,
  onBack,
}: {
  threadRef: ThreadRef;
  live: boolean;
  onBack: () => void;
}) {
  const demo = isTgDemoMode();
  const { data, isLoading } = useA2AMessages(threadRef.id, {
    refetchInterval: live || demo ? false : THREAD_POLL_MS,
    enabled: !demo,
  });
  const items = useMemo(
    () =>
      demo ? (DEMO_CHAT_MESSAGES[threadRef.id] ?? []) : (data?.items ?? []),
    [demo, threadRef.id, data],
  );

  const send = useSendCeoMessage();
  const reply = useReplyAsCeo();
  const markRead = useMarkConversationRead();

  // Fleet interjections go to one of the two participants — default to the
  // last non-CEO sender (the panel's rule), tap the chip to flip.
  const [replyTo, setReplyTo] = useState<string | null>(null);
  const fleetTarget = useMemo(() => {
    if (threadRef.kind !== "fleet") return null;
    if (replyTo) return replyTo;
    const lastOther = [...items]
      .reverse()
      .find((m) => m.from_agent !== CEO_SLUG);
    return lastOther?.from_agent ?? threadRef.a;
  }, [threadRef, replyTo, items]);

  // Opening a thread consumes its unread count (once per mount — the view
  // unmounts on back, so the ref guard is the whole story).
  const clearedRef = useRef(false);
  const markReadMutate = markRead.mutate;
  useEffect(() => {
    if (demo || clearedRef.current || threadRef.kind !== "mine") return;
    clearedRef.current = true;
    markReadMutate(threadRef.id);
  }, [demo, threadRef, markReadMutate]);

  // Stick to the bottom while the user is there; otherwise offer a jump
  // pill. State flips are scheduled post-paint (rAF) — layout reads plus a
  // sync setState in an effect would cascade renders.
  const scrollRef = useRef<HTMLDivElement>(null);
  const [showJump, setShowJump] = useState(false);
  const count = items.length;
  useEffect(() => {
    const raf = requestAnimationFrame(() => {
      const el = scrollRef.current;
      if (!el) return;
      const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
      if (nearBottom) el.scrollTop = el.scrollHeight;
      else setShowJump(true);
    });
    return () => cancelAnimationFrame(raf);
  }, [count]);

  const title =
    threadRef.kind === "mine"
      ? getAgentDisplayName(threadRef.peer)
      : `${getAgentDisplayName(threadRef.a)} ↔ ${getAgentDisplayName(threadRef.b)}`;
  const subtitle =
    threadRef.kind === "mine" ? threadRef.peer : "Watched conversation";

  const handleSend = (text: string) => {
    if (threadRef.kind === "mine") {
      send.mutate(
        { conversationId: threadRef.id, content: text },
        {
          onSuccess: () => haptics.success(),
          onError: (err) => {
            haptics.error();
            toast.error(getErrorMessage(err));
          },
        },
      );
    } else if (fleetTarget) {
      reply.mutate(
        { conversationId: threadRef.id, to_agent: fleetTarget, content: text },
        {
          onSuccess: () => haptics.success(),
          onError: (err) => {
            haptics.error();
            toast.error(getErrorMessage(err));
          },
        },
      );
    }
  };

  return (
    <TgSubPage title={title} subtitle={subtitle} onBack={onBack}>
      <div className="relative">
        <div
          ref={scrollRef}
          onScroll={(e) => {
            const el = e.currentTarget;
            if (el.scrollHeight - el.scrollTop - el.clientHeight < 120)
              setShowJump(false);
          }}
          className="max-h-[58dvh] space-y-1.5 overflow-y-auto pb-1"
        >
          {isLoading && !demo ? (
            <div className="space-y-2 py-2">
              {Array.from({ length: 3 }).map((_, i) => (
                <div
                  key={i}
                  className={cn(
                    "h-14 w-3/4 animate-pulse rounded-2xl bg-card",
                    i % 2 && "ml-auto",
                  )}
                />
              ))}
            </div>
          ) : !items.length ? (
            <p className="py-10 text-center text-sm text-muted-foreground">
              No messages yet. Say hello.
            </p>
          ) : threadRef.kind === "mine" ? (
            <MineTimeline items={items} />
          ) : (
            <FleetTimeline items={items} />
          )}
        </div>
        {showJump && (
          <button
            type="button"
            aria-label="Jump to latest"
            onClick={() => {
              const el = scrollRef.current;
              if (el) el.scrollTop = el.scrollHeight;
              setShowJump(false);
            }}
            className={cn(
              "absolute bottom-2 right-2 flex h-10 w-10 items-center justify-center rounded-full bg-card text-primary shadow-lg ring-1 ring-white/[0.08]",
              TG_PRESS,
            )}
          >
            <ArrowDown weight="bold" className="h-4 w-4" />
          </button>
        )}
      </div>

      {threadRef.kind === "fleet" && !threadRef.taskId ? (
        <div className="mt-2 flex items-center gap-2 rounded-2xl bg-muted/40 px-3.5 py-3 text-[13px] text-muted-foreground">
          <Eye className="h-4 w-4 shrink-0" />
          Watch-only. This thread isn&apos;t linked to a task, so replies
          can&apos;t be routed.
        </div>
      ) : (
        <>
          {threadRef.kind === "fleet" && fleetTarget && (
            <button
              type="button"
              onClick={() =>
                setReplyTo(
                  fleetTarget === threadRef.a ? threadRef.b : threadRef.a,
                )
              }
              className="mt-2 inline-flex items-center gap-1 rounded-full bg-muted/60 px-3 py-2 text-xs font-medium text-muted-foreground"
            >
              To: {getAgentDisplayName(fleetTarget)}
              <span className="text-muted-foreground/50">· tap to switch</span>
            </button>
          )}
          <Composer
            placeholder={demo ? "Demo mode, sends disabled" : "Message…"}
            pending={send.isPending || reply.isPending}
            disabled={demo}
            onSend={handleSend}
          />
        </>
      )}
    </TgSubPage>
  );
}

// ---------------------------------------------------------------------------
// Compose (new CEO DM)
// ---------------------------------------------------------------------------

function ComposeNewChat({
  onCreated,
  onCancel,
}: {
  onCreated: (id: string, peerSlug: string) => void;
  onCancel: () => void;
}) {
  const [target, setTarget] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const create = useCreateCeoConversation();

  const submit = () => {
    const trimmed = message.trim();
    if (!target || !trimmed || create.isPending) return;
    const targetAgent = target;
    haptics.tap();
    create.mutate(
      { target_agent: targetAgent, initial_message: trimmed },
      {
        onSuccess: (conversation) => {
          haptics.success();
          onCreated(conversation.id, targetAgent);
        },
        onError: (err) => {
          haptics.error();
          toast.error(getErrorMessage(err));
        },
      },
    );
  };

  return (
    <TgSubPage title="New chat" onBack={onCancel}>
      <div className="space-y-3">
        <AgentSelector
          value={target}
          onChange={setTarget}
          excludeRoles={EXCLUDE_NON_DM_ROLES}
          placeholder="Who do you want to message?"
          allowClear={false}
        />
        <div className={cn(TG_CARD, "p-1")}>
          <Textarea
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            placeholder="Type a message…"
            className="min-h-[110px] resize-none border-0 bg-transparent shadow-none focus-visible:ring-0"
            disabled={create.isPending}
          />
        </div>
        <button
          type="button"
          disabled={!target || !message.trim() || create.isPending}
          onClick={submit}
          className={cn(
            "flex w-full items-center justify-center gap-2 rounded-full bg-primary py-3 text-[15px] font-semibold text-primary-foreground disabled:opacity-40",
            TG_PRESS,
          )}
        >
          {create.isPending ? (
            <CircleNotch weight="bold" className="h-4 w-4 animate-spin" />
          ) : (
            <PaperPlaneTilt weight="fill" className="h-4 w-4" />
          )}
          Send
        </button>
      </div>
    </TgSubPage>
  );
}

// ---------------------------------------------------------------------------
// Tab root
// ---------------------------------------------------------------------------

export function TgChatTab() {
  const [view, setView] = useState<ChatView>({ mode: "list" });
  const [scope, setScope] = useState<"mine" | "fleet">("mine");
  // conversation_id → last WS-frame timestamp; keys row remounts so the
  // flash animation replays on every fresh frame.
  const [pulses, setPulses] = useState<Record<string, number>>({});
  const queryClient = useQueryClient();

  // Live wiring (the desktop A2A idiom): every persisted message announces
  // itself on /ws/system; invalidate-on-frame keeps REST the source of
  // truth. While the socket is up the thread poll switches off entirely.
  const { lastMessage, isConnected } = useA2ALiveStream();
  useEffect(() => {
    if (lastMessage?.type !== "a2a.message") return;
    void queryClient.invalidateQueries({
      queryKey: a2aLiveKeys.conversations,
    });
    void queryClient.invalidateQueries({
      queryKey: a2aLiveKeys.ceoConversations,
    });
    if (lastMessage.conversation_id) {
      const id = lastMessage.conversation_id;
      // Post-paint (rAF) — a sync setState in an effect cascades renders.
      const raf = requestAnimationFrame(() => {
        setPulses((p) => ({ ...p, [id]: Date.now() }));
      });
      void queryClient.invalidateQueries({
        queryKey: a2aLiveKeys.messages(id),
      });
      return () => cancelAnimationFrame(raf);
    }
  }, [lastMessage, queryClient]);

  // Events missed during a disconnect never replay — refetch everything on
  // reconnect (false → true only; initial mount doesn't fire).
  const prevConnected = useRef<boolean | null>(null);
  useEffect(() => {
    if (prevConnected.current === false && isConnected) {
      void queryClient.invalidateQueries({ queryKey: a2aLiveKeys.all });
    }
    prevConnected.current = isConnected;
  }, [isConnected, queryClient]);

  const openThread = (ref: ThreadRef) => {
    haptics.tap();
    setView({ mode: "thread", ref });
  };

  if (view.mode === "compose") {
    return (
      <ComposeNewChat
        onCreated={(id, peerSlug) =>
          setView({
            mode: "thread",
            ref: { kind: "mine", id, peer: peerSlug },
          })
        }
        onCancel={() => setView({ mode: "list" })}
      />
    );
  }

  if (view.mode === "thread") {
    return (
      <ThreadView
        threadRef={view.ref}
        live={isConnected}
        onBack={() => setView({ mode: "list" })}
      />
    );
  }

  if (view.mode === "secretary") {
    return <SecretaryView onBack={() => setView({ mode: "list" })} />;
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <div className="flex-1">
          <TgSegmented
            options={[
              { value: "mine", label: "Mine" },
              { value: "fleet", label: "Fleet" },
            ]}
            value={scope}
            onChange={setScope}
          />
        </div>
        <button
          type="button"
          aria-label="New chat"
          onClick={() => setView({ mode: "compose" })}
          className={cn(
            "flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground",
            TG_PRESS,
          )}
        >
          <NotePencil className="h-5 w-5" />
        </button>
      </div>
      {scope === "mine" && (
        <SecretaryPinnedRow
          onOpen={() => {
            haptics.tap();
            setView({ mode: "secretary" });
          }}
        />
      )}
      {scope === "mine" ? (
        <MineList pulses={pulses} onOpen={openThread} />
      ) : (
        <FleetList pulses={pulses} onOpen={openThread} />
      )}
    </div>
  );
}
