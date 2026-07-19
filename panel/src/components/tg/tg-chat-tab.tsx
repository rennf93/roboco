"use client";

import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  a2aLiveKeys,
  useA2AConversations,
  useA2AMessages,
  useCreateCeoConversation,
  useSendCeoMessage,
} from "@/hooks/use-a2a-live";
import { useA2ALiveStream } from "@/hooks/use-websocket";
import { CEO_SLUG } from "@/components/a2a/a2a-utils";
import { AgentSelector } from "@/components/agents/agent-selector";
import { EXCLUDE_NON_DM_ROLES } from "@/components/a2a/a2a-new-dm-dialog";
import { getAgentDisplayName } from "@/lib/agent-utils";
import { getErrorMessage } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import { TgAvatar } from "@/components/tg/ui";
import { ArrowLeft, MessageSquarePlus, Send } from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

/** Fallback cadence for the actively-viewed thread when the /ws/system
 * socket is down — live frames drive refresh whenever it's connected. */
const THREAD_POLL_MS = 10_000;

function ConversationList({
  onSelect,
  onCompose,
}: {
  onSelect: (id: string, peerLabel: string) => void;
  onCompose: () => void;
}) {
  const { data, isLoading } = useA2AConversations(50);

  return (
    <div className="space-y-2">
      <Button
        type="button"
        variant="outline"
        className="w-full justify-center gap-2"
        onClick={onCompose}
      >
        <MessageSquarePlus className="h-4 w-4" />
        New chat
      </Button>
      {isLoading ? (
        Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-14 w-full" />
        ))
      ) : !data?.items.length ? (
        <p className="py-8 text-center text-sm text-muted-foreground">
          No conversations yet
        </p>
      ) : (
        data.items.map((c) => {
          const peer = c.agent_a === CEO_SLUG ? c.agent_b : c.agent_a;
          const peerLabel = getAgentDisplayName(peer);
          return (
            <button
              key={c.id}
              type="button"
              onClick={() => onSelect(c.id, peerLabel)}
              className="flex w-full items-center gap-3 rounded-2xl border bg-card p-3 text-left text-card-foreground transition-colors active:bg-muted"
            >
              <TgAvatar name={peerLabel} />
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-sm font-medium">{peerLabel}</span>
                  {c.last_message_at && (
                    <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground">
                      {formatDistanceToNow(new Date(c.last_message_at))} ago
                    </span>
                  )}
                </div>
                {c.last_message_preview && (
                  <p className="truncate text-xs leading-snug text-muted-foreground">
                    {c.last_message_preview}
                  </p>
                )}
              </div>
            </button>
          );
        })
      )}
    </div>
  );
}

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
    create.mutate(
      { target_agent: targetAgent, initial_message: trimmed },
      {
        onSuccess: (conversation) => onCreated(conversation.id, targetAgent),
        onError: (err) => toast.error(getErrorMessage(err)),
      },
    );
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Button type="button" variant="ghost" size="icon" onClick={onCancel}>
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <span className="text-sm font-medium">New chat</span>
      </div>
      <AgentSelector
        value={target}
        onChange={setTarget}
        excludeRoles={EXCLUDE_NON_DM_ROLES}
        placeholder="Who do you want to message?"
        allowClear={false}
      />
      <Textarea
        value={message}
        onChange={(e) => setMessage(e.target.value)}
        placeholder="Type a message…"
        className="min-h-[90px] resize-none"
        disabled={create.isPending}
      />
      <Button
        type="button"
        className="w-full"
        disabled={!target || !message.trim() || create.isPending}
        onClick={submit}
      >
        <Send className="mr-2 h-4 w-4" />
        Send
      </Button>
    </div>
  );
}

function ThreadView({
  conversationId,
  peerLabel,
  live,
  onBack,
}: {
  conversationId: string;
  peerLabel: string;
  live: boolean;
  onBack: () => void;
}) {
  const { data, isLoading } = useA2AMessages(conversationId, {
    refetchInterval: live ? false : THREAD_POLL_MS,
  });
  const [draft, setDraft] = useState("");
  const send = useSendCeoMessage();

  const submit = () => {
    const trimmed = draft.trim();
    if (!trimmed || send.isPending) return;
    send.mutate(
      { conversationId, content: trimmed },
      {
        onSuccess: () => setDraft(""),
        onError: (err) => toast.error(getErrorMessage(err)),
      },
    );
  };

  // max-h (not flex-1/h-full) deliberately: the page root has no fixed
  // height (other tabs need the outer layout scroll, not a clipped one), so
  // a flex height chain here would have nothing definite to inherit. A
  // capped, independently-scrolling message region is the simplest thing
  // that actually scrolls regardless of ancestor height.
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2 border-b pb-2">
        <Button type="button" variant="ghost" size="icon" onClick={onBack}>
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <span className="font-medium">{peerLabel}</span>
      </div>
      <div className="max-h-[60dvh] space-y-2 overflow-y-auto py-1">
        {isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : !data?.items.length ? (
          <p className="py-8 text-center text-sm text-muted-foreground">
            No messages yet
          </p>
        ) : (
          data.items.map((m) => (
            <div
              key={m.id}
              className={cn(
                "max-w-[85%] rounded-lg px-3 py-2 text-sm",
                m.from_agent === CEO_SLUG
                  ? "ml-auto bg-primary text-primary-foreground"
                  : "bg-muted",
              )}
            >
              {m.content}
            </div>
          ))
        )}
      </div>
      <div className="flex items-end gap-2 border-t pt-2">
        <Textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Message…"
          className="min-h-[44px] resize-none"
          disabled={send.isPending}
        />
        <Button
          type="button"
          size="icon"
          disabled={!draft.trim() || send.isPending}
          onClick={submit}
        >
          <Send className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}

type ChatView =
  | { mode: "list" }
  | { mode: "compose" }
  | { mode: "thread"; id: string; peer: string };

/**
 * A2A chat for the CEO's phone: a conversation list, a compose-new-DM
 * picker, and a polled thread view — the mobile-scoped equivalent of the
 * desktop A2A admin page, built fresh rather than reusing its WS-wired,
 * switchboard-heavy components (not a fit for a single thumb column).
 */
export function TgChatTab() {
  const [view, setView] = useState<ChatView>({ mode: "list" });
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
    if (lastMessage.conversation_id) {
      void queryClient.invalidateQueries({
        queryKey: a2aLiveKeys.messages(lastMessage.conversation_id),
      });
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

  if (view.mode === "compose") {
    return (
      <ComposeNewChat
        onCreated={(id, peerSlug) =>
          setView({ mode: "thread", id, peer: getAgentDisplayName(peerSlug) })
        }
        onCancel={() => setView({ mode: "list" })}
      />
    );
  }

  if (view.mode === "thread") {
    return (
      <ThreadView
        conversationId={view.id}
        peerLabel={view.peer}
        live={isConnected}
        onBack={() => setView({ mode: "list" })}
      />
    );
  }

  return (
    <ConversationList
      onSelect={(id, peerLabel) => setView({ mode: "thread", id, peer: peerLabel })}
      onCompose={() => setView({ mode: "compose" })}
    />
  );
}
