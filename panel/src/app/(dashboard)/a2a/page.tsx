"use client";

import { Suspense, useCallback, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import {
  a2aLiveKeys,
  useA2AConversations,
  useA2AMessages,
} from "@/hooks/use-a2a-live";
import { useA2ALiveStream } from "@/hooks/use-websocket";
import { A2AConversationList } from "@/components/a2a/a2a-conversation-list";
import { A2ATranscript } from "@/components/a2a/a2a-transcript";
import { A2AReplyComposer } from "@/components/a2a/a2a-reply-composer";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { OfflineState } from "@/components/ui/offline-state";
import { getAgentDisplayName } from "@/lib/agent-utils";
import { lastSenderOf } from "@/components/a2a/a2a-utils";
import { cn } from "@/lib/utils";
import { MessagesSquare, Radio, RefreshCw } from "lucide-react";
import { formatDistanceToNow } from "date-fns";

function EmptyPanel({
  icon: Icon,
  message,
}: {
  icon: typeof MessagesSquare;
  message: string;
}) {
  return (
    <div className="h-full flex items-center justify-center text-muted-foreground">
      <div className="text-center p-4">
        <Icon className="h-8 w-8 mx-auto mb-2 opacity-50" />
        <p className="text-sm">{message}</p>
      </div>
    </div>
  );
}

function A2APageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();

  const selectedId = searchParams.get("conversation");

  const {
    data: conversationData,
    isLoading: loadingConversations,
    error,
    refetch: refetchConversations,
  } = useA2AConversations();
  const {
    data: messagesData,
    isLoading: loadingMessages,
    refetch: refetchMessages,
  } = useA2AMessages(selectedId);

  // Live wiring: every persisted A2A message is announced on /ws/system as an
  // `a2a.message` frame. Invalidate-on-frame (the session-detail idiom) — the
  // frame's excerpt is capped by design, so REST stays the source of truth and
  // react-query refetches the affected queries.
  const { lastMessage, isConnected } = useA2ALiveStream();
  useEffect(() => {
    if (lastMessage?.type !== "a2a.message") return;
    queryClient.invalidateQueries({ queryKey: a2aLiveKeys.conversations });
    if (selectedId && lastMessage.conversation_id === selectedId) {
      queryClient.invalidateQueries({
        queryKey: a2aLiveKeys.messages(selectedId),
      });
    }
  }, [lastMessage, queryClient, selectedId]);

  const handleSelect = useCallback(
    (id: string) => {
      const params = new URLSearchParams(searchParams.toString());
      params.set("conversation", id);
      router.push(`/a2a?${params.toString()}`);
    },
    [router, searchParams],
  );

  const handleRefresh = () => {
    refetchConversations();
    if (selectedId) refetchMessages();
  };

  const conversations = conversationData?.items ?? [];
  const selected = conversations.find((c) => c.id === selectedId) ?? null;
  const messages = messagesData?.items ?? [];
  const lastSender = lastSenderOf(messages);

  const isOffline =
    error &&
    (error.message?.includes("Network Error") ||
      (error as { code?: string })?.code === "ERR_NETWORK");

  return (
    <div className="flex flex-col lg:h-[calc(100vh-7rem)]">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">A2A Live</h1>
          <p className="text-muted-foreground">
            Live agent-to-agent conversations — watch and chime in
          </p>
        </div>
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <span
              className={cn(
                "h-2 w-2 rounded-full",
                isConnected
                  ? "bg-emerald-500 animate-pulse"
                  : "bg-muted-foreground/40",
              )}
            />
            <span className="text-xs text-muted-foreground">
              {isConnected ? "Live" : "Offline"}
            </span>
          </div>
          <Button variant="outline" onClick={handleRefresh}>
            <RefreshCw className="h-4 w-4 mr-2" />
            Refresh
          </Button>
        </div>
      </div>

      {isOffline ? (
        <OfflineState
          title="Cannot Load A2A Conversations"
          description="Start the RoboCo orchestrator to view agent-to-agent chats."
          onRetry={() => refetchConversations()}
        />
      ) : (
        <div className="grid grid-cols-12 gap-4 lg:gap-6 lg:flex-1 lg:min-h-0">
          {/* Panel 1: Conversations */}
          <Card className="col-span-12 lg:col-span-4 flex flex-col overflow-hidden">
            <CardContent className="p-3 flex flex-col h-full">
              <div className="flex items-center gap-2 mb-3 pb-2 border-b">
                <Radio className="h-4 w-4 text-muted-foreground" />
                <span className="text-sm font-medium">Conversations</span>
              </div>
              <div className="flex-1 overflow-hidden -mx-3">
                <A2AConversationList
                  conversations={conversations}
                  selectedId={selectedId}
                  onSelect={handleSelect}
                  isLoading={loadingConversations}
                />
              </div>
            </CardContent>
          </Card>

          {/* Panel 2: Transcript + composer */}
          <Card className="col-span-12 lg:col-span-8 flex flex-col overflow-hidden">
            <CardContent className="p-3 flex flex-col h-full">
              {selected ? (
                <>
                  <div className="flex items-center gap-2 mb-3 pb-2 border-b flex-wrap">
                    <MessagesSquare className="h-4 w-4 text-muted-foreground" />
                    <span className="text-sm font-medium">
                      {getAgentDisplayName(selected.agent_a)}
                      {" ↔ "}
                      {getAgentDisplayName(selected.agent_b)}
                    </span>
                    <Badge
                      variant={
                        selected.status === "active" ? "default" : "secondary"
                      }
                      className="text-xs"
                    >
                      {selected.status}
                    </Badge>
                    <span className="text-xs text-muted-foreground ml-auto">
                      {selected.message_count} msgs · updated{" "}
                      {formatDistanceToNow(new Date(selected.updated_at))} ago
                    </span>
                  </div>
                  <div className="flex-1 overflow-hidden -mx-3">
                    <A2ATranscript
                      messages={messages}
                      isLoading={loadingMessages}
                    />
                  </div>
                  {/* Reply composer. The backend's reply route rejects with
                      400 exactly when the watched conversation has no task
                      link (replies ride the gateway send path, which requires
                      one), so a task-less conversation is read-only — say why
                      instead of letting the send bounce. Status does NOT gate
                      the composer: the CEO's reply lands in their own direct
                      thread with the participant, not in this conversation. */}
                  <div className="shrink-0 border-t -mx-3">
                    {selected.task_id ? (
                      <A2AReplyComposer
                        key={selected.id}
                        conversationId={selected.id}
                        agentA={selected.agent_a}
                        agentB={selected.agent_b}
                        lastSender={lastSender}
                      />
                    ) : (
                      <div className="p-4 text-center text-sm text-muted-foreground">
                        This conversation has no linked task, so a reply
                        can&apos;t be sent (A2A messages are always scoped to a
                        task).
                      </div>
                    )}
                  </div>
                </>
              ) : (
                <EmptyPanel
                  icon={MessagesSquare}
                  message="Select a conversation to watch it live"
                />
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}

// Wrap in Suspense for useSearchParams
export default function A2APage() {
  return (
    <Suspense
      fallback={
        <div className="flex flex-col lg:h-[calc(100vh-7rem)]">
          <div className="flex items-center justify-between mb-4">
            <div>
              <Skeleton className="h-9 w-48 mb-2" />
              <Skeleton className="h-5 w-64" />
            </div>
          </div>
          <div className="grid grid-cols-12 gap-4 lg:gap-6">
            <Card className="col-span-12 lg:col-span-4">
              <CardContent className="p-3 space-y-2">
                {Array.from({ length: 6 }).map((_, i) => (
                  <Skeleton key={i} className="h-20 w-full" />
                ))}
              </CardContent>
            </Card>
            <Card className="col-span-12 lg:col-span-8" />
          </div>
        </div>
      }
    >
      <A2APageContent />
    </Suspense>
  );
}
