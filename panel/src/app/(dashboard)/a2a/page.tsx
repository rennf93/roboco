"use client";

import {
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import {
  a2aLiveKeys,
  useA2AAdminPairs,
  useA2AConversations,
  useA2AMessages,
} from "@/hooks/use-a2a-live";
import { useA2ALiveStream } from "@/hooks/use-websocket";
import { usePageRefresh } from "@/hooks";
import { A2AConversationList } from "@/components/a2a/a2a-conversation-list";
import { A2ASwitchboard } from "@/components/a2a/a2a-switchboard";
import { A2ATranscript } from "@/components/a2a/a2a-transcript";
import { A2AReplyComposer } from "@/components/a2a/a2a-reply-composer";
import { A2AFilterBar } from "@/components/a2a/a2a-filter-bar";
import { A2AContextPane } from "@/components/a2a/a2a-context-pane";
import {
  A2AConnectionBadge,
  A2AConnectionBanner,
} from "@/components/a2a/a2a-connection-badge";
import { latestPulseTimestamps } from "@/components/a2a/a2a-switchboard-utils";
import {
  distinctA2AAgents,
  filterConversations,
  filterPairs,
  EMPTY_A2A_FILTERS,
  type A2AFilters,
} from "@/components/a2a/a2a-filter-utils";
import type { AdminPairSummary } from "@/lib/api/a2a";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { OfflineState } from "@/components/ui/offline-state";
import { HelpTip } from "@/components/ui/help-tip";
import { useUIStore } from "@/store";
import { getAgentDisplayName } from "@/lib/agent-utils";
import { lastSenderOf } from "@/components/a2a/a2a-utils";
import { cn } from "@/lib/utils";
import {
  ArrowLeft,
  LayoutGrid,
  List as ListIcon,
  MessagesSquare,
  PanelRightClose,
  PanelRightOpen,
  Radio,
} from "lucide-react";
import { formatDistanceToNow } from "date-fns";

type A2AView = "switchboard" | "list";

interface PeekedPair {
  agent_a: string;
  agent_b: string;
}

function A2APageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();

  const selectedId = searchParams.get("conversation");

  // Desktop default is the switchboard (org-chart pair cards); the classic
  // list stays one click away as the mobile/compact fallback.
  const [view, setView] = useState<A2AView>("switchboard");
  // A pair with no conversation yet, clicked from the switchboard — there is
  // nothing to select via `?conversation=`, so it's tracked separately and
  // shown as an explicit "no A2A yet" state in the drill-in panel.
  const [peekedPair, setPeekedPair] = useState<PeekedPair | null>(null);

  // Filter panel: Agent, Task (id fragment + no-linked-task), Status, Date
  // range — narrows both the switchboard's pairs (Agent only) and the
  // list's conversations (all four), per the design doc's per-view rules.
  const [filters, setFilters] = useState<A2AFilters>(EMPTY_A2A_FILTERS);

  // xl:+ context pane collapse, persisted via the shared UI store — same
  // idiom as sidebar/theme preferences (design doc §1).
  const contextOpen = useUIStore((s) => s.a2aContextOpen);
  const toggleContext = useUIStore((s) => s.toggleA2AContext);

  // Reconnecting/disconnected banner strip, dismissable per occurrence — it
  // reappears the next time the connection drops (design doc §3). Render-phase
  // reset (compared against the previous connectionState, same idiom as
  // A2APairCard's usePulseFlash) rather than an effect.
  const [bannerDismissed, setBannerDismissed] = useState(false);
  const [lastConnectionState, setLastConnectionState] = useState<string | null>(
    null,
  );

  const {
    data: conversationData,
    isLoading: loadingConversations,
    error,
    refetch: refetchConversations,
  } = useA2AConversations(100);
  const {
    data: pairsData,
    isLoading: loadingPairs,
    refetch: refetchPairs,
  } = useA2AAdminPairs();
  const {
    data: messagesData,
    isLoading: loadingMessages,
    error: messagesError,
    refetch: refetchMessages,
  } = useA2AMessages(selectedId);

  const { register, unregister, refresh } = usePageRefresh();

  useEffect(() => {
    const callbacks = [
      () => {
        void refetchConversations();
      },
      () => {
        void refetchPairs();
      },
      () => {
        void refetchMessages();
      },
    ];
    callbacks.forEach((cb) => register(cb));
    return () => {
      callbacks.forEach((cb) => unregister(cb));
    };
  }, [
    register,
    unregister,
    refetchConversations,
    refetchPairs,
    refetchMessages,
  ]);

  // Live wiring: every persisted A2A message is announced on /ws/system as an
  // `a2a.message` frame. Invalidate-on-frame (the session-detail idiom) — the
  // frame's excerpt is capped by design, so REST stays the source of truth and
  // react-query refetches the affected queries.
  const {
    lastMessage,
    a2aMessages,
    isConnected,
    state: connectionState,
  } = useA2ALiveStream();
  useEffect(() => {
    if (lastMessage?.type !== "a2a.message") return;
    queryClient.invalidateQueries({ queryKey: a2aLiveKeys.conversations });
    queryClient.invalidateQueries({ queryKey: a2aLiveKeys.pairs });
    if (selectedId && lastMessage.conversation_id === selectedId) {
      queryClient.invalidateQueries({
        queryKey: a2aLiveKeys.messages(selectedId),
      });
    }
  }, [lastMessage, queryClient, selectedId]);

  // Re-arm the dismissable banner the next time the connection actually
  // drops, rather than leaving it dismissed forever after the first hiccup.
  if (connectionState !== lastConnectionState) {
    setLastConnectionState(connectionState);
    if (
      connectionState !== "reconnecting" &&
      connectionState !== "disconnected"
    ) {
      setBannerDismissed(false);
    }
  }

  // On /ws/system reconnect (false → true) the A2A list is stale — events
  // missed during the disconnect. Invalidate the a2a query family so
  // react-query refetches. Initial mount with isConnected=true does NOT
  // fire (prevConnected starts unknown, not false).
  const prevConnected = useRef<boolean | null>(null);
  useEffect(() => {
    if (prevConnected.current === false && isConnected) {
      queryClient.invalidateQueries({ queryKey: a2aLiveKeys.all });
    }
    prevConnected.current = isConnected;
  }, [isConnected, queryClient]);

  const pairs = useMemo(() => pairsData?.items ?? [], [pairsData]);
  // Activity = A2A only: derived purely from a2a.message frames, never from
  // verb/flow traffic on the same /ws/system stream.
  const pulses = useMemo(
    () => latestPulseTimestamps(a2aMessages, pairs),
    [a2aMessages, pairs],
  );
  const filteredPairs = useMemo(
    () => filterPairs(pairs, filters),
    [pairs, filters],
  );

  const handleSelect = useCallback(
    (id: string) => {
      setPeekedPair(null);
      const params = new URLSearchParams(searchParams.toString());
      params.set("conversation", id);
      router.push(`/a2a?${params.toString()}`);
    },
    [router, searchParams],
  );

  const handleOpenPair = useCallback(
    (pair: AdminPairSummary) => {
      if (pair.conversation_id) {
        handleSelect(pair.conversation_id);
        return;
      }
      // Never-talked pair: nothing to select, clear any prior selection and
      // show the pair's own empty state instead.
      setPeekedPair({ agent_a: pair.agent_a, agent_b: pair.agent_b });
      const params = new URLSearchParams(searchParams.toString());
      params.delete("conversation");
      const qs = params.toString();
      router.push(qs ? `/a2a?${qs}` : "/a2a");
    },
    [handleSelect, router, searchParams],
  );

  const conversations = useMemo(
    () => conversationData?.items ?? [],
    [conversationData],
  );
  const filteredConversations = useMemo(
    () => filterConversations(conversations, filters),
    [conversations, filters],
  );
  const agentOptions = useMemo(
    () => distinctA2AAgents(conversations, pairs),
    [conversations, pairs],
  );
  const selected = conversations.find((c) => c.id === selectedId) ?? null;
  const messages = messagesData?.items ?? [];
  const lastSender = lastSenderOf(messages);

  const isOffline =
    error &&
    (error.message?.includes("Network Error") ||
      (error as { code?: string })?.code === "ERR_NETWORK");

  // Below `lg` only one pane shows at a time (list/switchboard -> detail with
  // a back affordance); at `lg`+ both always show side by side.
  const onDetailLevel = !!selectedId || !!peekedPair;
  const handleBack = useCallback(() => {
    setPeekedPair(null);
    const params = new URLSearchParams(searchParams.toString());
    params.delete("conversation");
    const qs = params.toString();
    router.push(qs ? `/a2a?${qs}` : "/a2a");
  }, [router, searchParams]);

  return (
    // h-dvh (not h-vh) and unconditional now (not just lg:+) so the single
    // visible mobile pane gets a real height for its internal ScrollArea.
    <div className="flex flex-col h-[calc(100dvh-7rem)]">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">A2A Live</h1>
          <p className="text-muted-foreground">
            Live agent-to-agent conversations — watch and chime in
          </p>
        </div>
        <div className="flex items-center gap-4">
          <A2AConnectionBadge state={connectionState} />
          {/* Context pane never appears below xl — its toggle is hidden
              there too, matching the switchboard/list toggle's placement
              idiom (design doc §1). */}
          <HelpTip
            label={contextOpen ? "Hide context panel" : "Show context panel"}
          >
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="hidden h-7 px-2 xl:inline-flex"
              onClick={toggleContext}
              aria-label={
                contextOpen ? "Hide context panel" : "Show context panel"
              }
              title={contextOpen ? "Hide context panel" : "Show context panel"}
            >
              {contextOpen ? (
                <PanelRightClose className="h-3.5 w-3.5" />
              ) : (
                <PanelRightOpen className="h-3.5 w-3.5" />
              )}
            </Button>
          </HelpTip>
        </div>
      </div>

      {isOffline ? (
        <OfflineState
          title="Cannot Load A2A Conversations"
          description="Start the RoboCo orchestrator to view agent-to-agent chats."
          onRetry={() => void refresh()}
        />
      ) : (
        <>
          {/* Mobile-only back affordance — drills back up to the list. */}
          {onDetailLevel && (
            <HelpTip label="Returns to the switchboard/list">
              <Button
                variant="ghost"
                size="sm"
                className="mb-2 w-fit shrink-0 lg:hidden"
                onClick={handleBack}
              >
                <ArrowLeft className="h-4 w-4 mr-2" />
                Back
              </Button>
            </HelpTip>
          )}

          <div className="grid flex-1 min-h-0 grid-cols-12 gap-4 lg:gap-6">
            {/* Panel 1: Switchboard (default) / classic conversation list */}
            <Card
              className={cn(
                "col-span-12 flex-col overflow-hidden lg:col-span-4 lg:flex",
                contextOpen && "xl:col-span-3",
                onDetailLevel ? "hidden" : "flex",
              )}
            >
              <CardContent className="p-3 flex flex-col h-full">
                <div className="flex items-center gap-2 mb-3 pb-2 border-b">
                  <Radio className="h-4 w-4 text-muted-foreground" />
                  <span className="text-sm font-medium">
                    {view === "switchboard" ? "Switchboard" : "Conversations"}
                  </span>
                  <HelpTip label="Switch between the org-chart switchboard and the classic conversation list">
                    <div className="ml-auto flex items-center gap-1">
                      <Button
                        type="button"
                        variant={view === "switchboard" ? "secondary" : "ghost"}
                        size="sm"
                        className="h-7 px-2"
                        aria-pressed={view === "switchboard"}
                        aria-label="Switchboard: org-chart pair cards"
                        onClick={() => setView("switchboard")}
                        title="Switchboard: org-chart pair cards"
                      >
                        <LayoutGrid className="h-3.5 w-3.5" />
                      </Button>
                      <Button
                        type="button"
                        variant={view === "list" ? "secondary" : "ghost"}
                        size="sm"
                        className="h-7 px-2"
                        aria-pressed={view === "list"}
                        aria-label="Classic conversation list"
                        onClick={() => setView("list")}
                        title="Classic conversation list"
                      >
                        <ListIcon className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </HelpTip>
                </div>
                <A2AFilterBar
                  filters={filters}
                  onFiltersChange={setFilters}
                  agentOptions={agentOptions}
                  view={view}
                />
                <div className="flex-1 overflow-hidden -mx-3">
                  {view === "switchboard" ? (
                    <A2ASwitchboard
                      pairs={filteredPairs}
                      pulses={pulses}
                      selectedConversationId={selectedId}
                      isLoading={loadingPairs}
                      onOpenPair={handleOpenPair}
                    />
                  ) : (
                    <A2AConversationList
                      conversations={filteredConversations}
                      selectedId={selectedId}
                      onSelect={handleSelect}
                      isLoading={loadingConversations}
                      pulses={pulses}
                    />
                  )}
                </div>
              </CardContent>
            </Card>

            {/* Panel 2: Transcript + composer */}
            <Card
              className={cn(
                "col-span-12 flex-col overflow-hidden lg:col-span-8 lg:flex",
                contextOpen && "xl:col-span-6",
                onDetailLevel ? "flex" : "hidden",
              )}
            >
              <CardContent className="p-3 flex flex-col h-full">
                {peekedPair ? (
                  <div className="h-full flex items-center justify-center text-muted-foreground">
                    <div className="text-center p-4 max-w-xs">
                      <MessagesSquare className="h-8 w-8 mx-auto mb-2 opacity-50" />
                      <p className="text-sm">
                        {getAgentDisplayName(peekedPair.agent_a)} and{" "}
                        {getAgentDisplayName(peekedPair.agent_b)} haven&apos;t
                        A2A&apos;d each other yet.
                      </p>
                    </div>
                  </div>
                ) : (
                  <>
                    {selected && (
                      <div className="flex items-center gap-2 mb-3 pb-2 border-b flex-wrap">
                        <MessagesSquare className="h-4 w-4 text-muted-foreground" />
                        <span className="text-sm font-medium">
                          {getAgentDisplayName(selected.agent_a)}
                          {" ↔ "}
                          {getAgentDisplayName(selected.agent_b)}
                        </span>
                        <HelpTip label={selected.status === "active" ? "Actively exchanging messages" : "No longer active"}>
                          <Badge
                            variant={
                              selected.status === "active"
                                ? "default"
                                : "secondary"
                            }
                            className="text-xs w-fit"
                          >
                            {selected.status}
                          </Badge>
                        </HelpTip>
                        <HelpTip label={new Date(selected.updated_at).toLocaleString()}>
                          <span className="text-xs text-muted-foreground ml-auto w-fit">
                            {selected.message_count} msgs · updated{" "}
                            {formatDistanceToNow(new Date(selected.updated_at))}{" "}
                            ago
                          </span>
                        </HelpTip>
                      </div>
                    )}
                    {/* Scoped to the stream pane, not a full-page takeover —
                      a live-connection hint, distinct from OfflineState. */}
                    {(connectionState === "reconnecting" ||
                      connectionState === "disconnected") &&
                      !bannerDismissed && (
                        <div className="-mx-3 mb-3">
                          <A2AConnectionBanner
                            state={connectionState}
                            onDismiss={() => setBannerDismissed(true)}
                          />
                        </div>
                      )}
                    {/* All three loading/empty/error states live inside
                      A2ATranscript now — the pane chrome above stays mounted
                      and stable while only this area swaps (design doc §5). */}
                    <div className="flex-1 overflow-hidden -mx-3">
                      <A2ATranscript
                        messages={messages}
                        isLoading={loadingMessages}
                        hasSelection={!!selected}
                        error={!!messagesError}
                        onRetry={() => void refetchMessages()}
                      />
                    </div>
                    {/* Reply composer. The backend's reply route rejects with
                      400 exactly when the watched conversation has no task
                      link (replies ride the gateway send path, which requires
                      one), so a task-less conversation is read-only — say why
                      instead of letting the send bounce. Status does NOT gate
                      the composer: the CEO's reply lands in their own direct
                      thread with the participant, not in this conversation. */}
                    {selected && (
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
                            can&apos;t be sent (A2A messages are always scoped
                            to a task).
                          </div>
                        )}
                      </div>
                    )}
                  </>
                )}
              </CardContent>
            </Card>

            {/* Panel 3: Context (xl:+ only, dismissible) — participant
              identity cards + linked-task summary, read-only (design doc
              §1). */}
            {contextOpen && (
              <Card className="hidden xl:col-span-3 xl:flex xl:flex-col overflow-hidden">
                <CardContent className="p-0 flex-1 overflow-y-auto">
                  {selected ? (
                    <A2AContextPane
                      agentA={selected.agent_a}
                      agentB={selected.agent_b}
                      taskId={selected.task_id}
                    />
                  ) : peekedPair ? (
                    <A2AContextPane
                      agentA={peekedPair.agent_a}
                      agentB={peekedPair.agent_b}
                      taskId={null}
                    />
                  ) : (
                    <div className="h-full flex items-center justify-center text-muted-foreground p-4 text-center text-sm">
                      Select a conversation to see participant details
                    </div>
                  )}
                </CardContent>
              </Card>
            )}
          </div>
        </>
      )}
    </div>
  );
}

// Wrap in Suspense for useSearchParams
export default function A2APage() {
  return (
    <Suspense
      fallback={
        <div className="flex flex-col h-[calc(100dvh-7rem)]">
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
