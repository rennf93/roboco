"use client";

import { useEffect, useRef } from "react";
import { useParams, useSearchParams } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useSession, useSessionMessages } from "@/hooks/use-channels";
import { messagesApi } from "@/lib/api/messages";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { MessageComposer } from "@/components/communications/message-composer";
import { MessageTypeBadge } from "@/components/communications/message-type-badge";
import { Markdown } from "@/components/ui/markdown";
import { getAgentDisplayName, getAgentInitials } from "@/lib/agent-utils";
import {
  ArrowLeft,
  MessageSquare,
  ListTodo,
  Clock,
  Hash,
  RefreshCw,
} from "lucide-react";
import { CopyButton } from "@/components/ui/copy-button";
import { formatDistanceToNow, format } from "date-fns";
import { toast } from "sonner";
import Link from "next/link";
import { Suspense } from "react";

function SessionDetailContent() {
  const params = useParams();
  const searchParams = useSearchParams();
  const sessionId = params.sessionId as string;

  // Read navigation context from URL params
  const channelId = searchParams.get("channel");
  const groupId = searchParams.get("group");

  // Build back URL preserving context
  const backUrl = channelId && groupId
    ? `/communications?channel=${channelId}&group=${groupId}`
    : "/communications";
  const queryClient = useQueryClient();
  const scrollRef = useRef<HTMLDivElement>(null);

  // Fetch session details and messages.
  //
  // The message query loads the transcript once and then holds it (staleTime
  // Infinity, no focus/reconnect refetch), so an OPEN session is read exactly
  // once and a CLOSED session's immutable transcript stays loaded for review.
  // The hooks treat a 404 (reaped session) as terminal and never retry it, which
  // is what stops the panel from accumulating a 404 storm across every dead
  // session it has opened. `refetchMessages` (the manual Refresh button) stays
  // available for live sessions.
  const { data: session, isLoading: loadingSession, refetch: refetchSession } = useSession(sessionId);
  const { data: messagesData, isLoading: loadingMessages, refetch: refetchMessages } = useSessionMessages(sessionId);

  // Sort messages chronologically (oldest first for chat UI)
  const messages = [...(messagesData?.items || [])].sort(
    (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()
  );

  // Track if we've done the initial scroll
  const hasScrolledRef = useRef(false);

  // Auto-scroll to bottom only once on initial load
  useEffect(() => {
    if (scrollRef.current && messages.length > 0 && !hasScrolledRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
      hasScrolledRef.current = true;
    }
  }, [messages.length]);

  // Send message mutation
  const sendMessage = useMutation({
    mutationFn: async ({ content, type }: { content: string; type: string }) => {
      return messagesApi.send(sessionId, content, type);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["messages", "list", sessionId] });
      toast.success("Message sent");
    },
    onError: (error: Error) => {
      toast.error("Failed to send message: " + error.message);
    },
  });

  const handleSendMessage = (message: { content: string; type: string }) => {
    sendMessage.mutate(message);
  };

  const handleRefresh = () => {
    refetchSession();
    refetchMessages();
  };

  // Get primary task
  const primaryTask = session?.task_links?.find(t => t.is_primary) || session?.task_links?.[0];

  if (loadingSession) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-96 w-full" />
      </div>
    );
  }

  if (!session) {
    return (
      <div className="space-y-6">
        <Link href={backUrl}>
          <Button variant="ghost" size="sm">
            <ArrowLeft className="h-4 w-4 mr-2" />
            Back to Communications
          </Button>
        </Link>
        <Card>
          <CardContent className="pt-6">
            <div className="text-center py-12">
              <MessageSquare className="h-12 w-12 mx-auto mb-4 text-muted-foreground/50" />
              <h3 className="text-lg font-medium mb-2">Session Not Found</h3>
              <p className="text-sm text-muted-foreground">
                The session you&apos;re looking for doesn&apos;t exist or has been deleted.
              </p>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-[calc(100vh-7rem)]">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-4">
          <Link href={backUrl}>
            <Button variant="ghost" size="sm">
              <ArrowLeft className="h-4 w-4 mr-2" />
              Back
            </Button>
          </Link>
          <div>
            <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
              <MessageSquare className="h-6 w-6" />
              Session {sessionId.slice(0, 8)}
            </h1>
            <p className="text-muted-foreground text-sm">
              Started {formatDistanceToNow(new Date(session.started_at))} ago
            </p>
          </div>
        </div>
        <Button variant="outline" onClick={handleRefresh}>
          <RefreshCw className="h-4 w-4 mr-2" />
          Refresh
        </Button>
      </div>

      {/* Session Info Bar */}
      <Card className="mb-4 shrink-0">
        <CardContent className="py-3">
          <div className="flex items-center gap-4 flex-wrap">
            <Badge variant={session.status === "active" ? "default" : "secondary"}>
              {session.status}
            </Badge>
            <div className="flex items-center gap-1 text-sm text-muted-foreground">
              <MessageSquare className="h-4 w-4" />
              {session.message_count} messages
            </div>
            <div className="flex items-center gap-1 text-sm text-muted-foreground">
              <Clock className="h-4 w-4" />
              {format(new Date(session.started_at), "MMM d, yyyy h:mm a")}
            </div>
            {session.closed_at && (
              <div className="flex items-center gap-1 text-sm text-muted-foreground">
                Closed: {format(new Date(session.closed_at), "MMM d, yyyy h:mm a")}
              </div>
            )}

            {/* Linked Tasks */}
            {session.task_links && session.task_links.length > 0 && (
              <>
                <span className="text-muted-foreground">|</span>
                <div className="flex items-center gap-2">
                  <ListTodo className="h-4 w-4 text-muted-foreground" />
                  {primaryTask && (
                    <Link
                      href={`/tasks/${primaryTask.task_id}`}
                      className="text-sm text-primary hover:underline"
                    >
                      {primaryTask.task_title || `Task ${primaryTask.task_id.slice(0, 8)}`}
                    </Link>
                  )}
                  {session.task_links.length > 1 && (
                    <Badge variant="outline" className="text-xs">
                      +{session.task_links.length - 1} more
                    </Badge>
                  )}
                </div>
              </>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Messages Area */}
      <Card className="flex-1 flex flex-col min-h-0">
        <CardHeader className="pb-2 shrink-0">
          <CardTitle className="text-sm flex items-center gap-2">
            <Hash className="h-4 w-4" />
            Messages
          </CardTitle>
        </CardHeader>
        <CardContent className="flex-1 flex flex-col p-0 min-h-0">
          {/* Messages List */}
          <div ref={scrollRef} className="flex-1 overflow-y-auto p-4">
            {loadingMessages ? (
              <div className="space-y-4">
                {Array.from({ length: 5 }).map((_, i) => (
                  <div key={i} className="flex gap-3">
                    <Skeleton className="h-8 w-8 rounded-full" />
                    <div className="flex-1">
                      <Skeleton className="h-4 w-32 mb-2" />
                      <Skeleton className="h-12 w-full" />
                    </div>
                  </div>
                ))}
              </div>
            ) : messages.length === 0 ? (
              <div className="text-center py-12 text-muted-foreground">
                <MessageSquare className="h-12 w-12 mx-auto mb-2 opacity-50" />
                <p>No messages in this session</p>
                <p className="text-sm">Use the composer below to start the conversation</p>
              </div>
            ) : (
              <div className="space-y-3">
                {messages.map((message) => (
                  <div
                    key={message.id}
                    className="group relative flex gap-3 p-3 rounded-lg border bg-card hover:bg-muted/30 transition-colors"
                  >
                    <div className="h-9 w-10 rounded-lg bg-primary/10 flex items-center justify-center shrink-0 border">
                      <span className="text-[10px] font-bold tracking-tight">
                        {getAgentInitials(message.agent_id)}
                      </span>
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1.5">
                        <span className="font-semibold text-sm">
                          {getAgentDisplayName(message.agent_id)}
                        </span>
                        <MessageTypeBadge type={message.type} />
                        <span className="text-xs text-muted-foreground ml-auto">
                          {formatDistanceToNow(new Date(message.timestamp))} ago
                        </span>
                      </div>
                      <div className="text-sm prose prose-sm dark:prose-invert max-w-none">
                        <Markdown>{message.content}</Markdown>
                      </div>
                    </div>
                    {/* Copy button — visible on hover */}
                    <CopyButton
                      value={message.content}
                      className="absolute right-2 top-2 opacity-0 transition-opacity group-hover:opacity-100"
                    />
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Message Composer */}
          <div className="shrink-0 border-t">
            <MessageComposer
              channelId={sessionId}
              onSend={handleSendMessage}
              isSending={sendMessage.isPending}
            />
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

// Wrap in Suspense for useSearchParams
export default function SessionDetailPage() {
  return (
    <Suspense
      fallback={
        <div className="space-y-6">
          <Skeleton className="h-10 w-64" />
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-96 w-full" />
        </div>
      }
    >
      <SessionDetailContent />
    </Suspense>
  );
}
