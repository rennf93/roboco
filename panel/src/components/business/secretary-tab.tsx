"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Check, Loader2, Send, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import { OfflineState } from "@/components/ui/offline-state";
import { Markdown } from "@/components/ui/markdown";
import { RequiredNotesDialog } from "@/components/ui/required-notes-dialog";
import { getErrorMessage } from "@/lib/api/client";
import { secretaryApi, type SecretaryDirective } from "@/lib/api/secretary";
import { useSecretary } from "@/hooks/use-secretary";

// ---------------------------------------------------------------------------
// Directive card — structured key-value rows, no raw JSON
// ---------------------------------------------------------------------------

interface DirectiveCardProps {
  directive: SecretaryDirective;
  onConfirm: (id: string) => void;
  onReject: (id: string, reason: string) => void;
  busy: boolean;
}

function DirectiveCard({ directive, onConfirm, onReject, busy }: DirectiveCardProps) {
  const [rejectOpen, setRejectOpen] = useState(false);
  const payloadKeys = Object.keys(directive.payload);

  return (
    <>
      <div className="space-y-3 rounded-lg border p-3">
        {/* Header row */}
        <div className="flex items-center justify-between">
          <span className="text-sm font-semibold">{directive.kind}</span>
          <span className="text-xs text-muted-foreground capitalize">
            {directive.status}
          </span>
        </div>

        {/* Structured payload — one labeled row per key */}
        {payloadKeys.length > 0 ? (
          <div className="space-y-1.5">
            {payloadKeys.map((key) => (
              <div key={key} className="flex flex-wrap gap-x-3 text-sm">
                <span className="min-w-[8rem] text-xs font-medium text-muted-foreground capitalize">
                  {key.replace(/_/g, " ")}
                </span>
                <span className="text-xs break-all">
                  {String(directive.payload[key] ?? "—")}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">No payload.</p>
        )}

        {/* Actions */}
        <div className="flex gap-2 pt-1">
          <Button
            size="sm"
            disabled={busy}
            onClick={() => onConfirm(directive.id)}
          >
            <Check className="mr-1 h-4 w-4" /> Confirm
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => setRejectOpen(true)}
          >
            <X className="mr-1 h-4 w-4" /> Reject
          </Button>
        </div>
      </div>

      {/* Reject requires a non-empty reason */}
      <RequiredNotesDialog
        open={rejectOpen}
        onOpenChange={setRejectOpen}
        title="Reject directive"
        description="Please provide a reason for rejecting this directive."
        notesLabel="Reason"
        placeholder="Why are you rejecting this directive?"
        submitLabel="Reject"
        isPending={busy}
        onSubmit={(reason) => {
          setRejectOpen(false);
          onReject(directive.id, reason);
        }}
      />
    </>
  );
}

// ---------------------------------------------------------------------------
// Directives panel skeleton
// ---------------------------------------------------------------------------

function DirectivesSkeleton() {
  return (
    <div className="space-y-3">
      {[1, 2].map((i) => (
        <div key={i} className="rounded-lg border p-3 space-y-2">
          <div className="flex items-center justify-between">
            <Skeleton className="h-4 w-24" />
            <Skeleton className="h-3 w-16" />
          </div>
          <Skeleton className="h-3 w-full" />
          <Skeleton className="h-3 w-3/4" />
          <div className="flex gap-2 pt-1">
            <Skeleton className="h-8 w-20" />
            <Skeleton className="h-8 w-20" />
          </div>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Chat messages — ReactMarkdown in styled bubbles
// ---------------------------------------------------------------------------

function ChatMessages({
  messages,
  streaming,
}: {
  messages: { role: "user" | "assistant"; text: string }[];
  streaming: boolean;
}) {
  return (
    <div className="flex-1 space-y-3 overflow-y-auto">
      {messages.length === 0 && (
        <p className="text-sm text-muted-foreground">
          Say something to start the conversation.
        </p>
      )}
      {messages.map((m, i) => (
        <div
          key={i}
          className={
            m.role === "user"
              ? "ml-auto max-w-[80%] rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground"
              : "mr-auto max-w-[80%] rounded-lg bg-muted px-3 py-2"
          }
        >
          {m.role === "user" ? (
            <span>{m.text}</span>
          ) : (
            <Markdown className="text-sm">{m.text}</Markdown>
          )}
        </div>
      ))}
      {streaming && (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="h-3 w-3 animate-spin" /> thinking…
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Public export
// ---------------------------------------------------------------------------

export function SecretaryTab() {
  const qc = useQueryClient();
  const { sessionId, messages, streaming, start, send, stop } = useSecretary();
  const [input, setInput] = useState("");
  const [starting, setStarting] = useState(false);

  const {
    data: pending = [],
    isLoading: directivesLoading,
    isError: directivesError,
    refetch: refetchDirectives,
  } = useQuery({
    queryKey: ["secretary", "directives", "pending"],
    queryFn: () => secretaryApi.listDirectives("pending"),
    refetchInterval: 15000,
  });

  const confirmMutation = useMutation({
    mutationFn: (id: string) => secretaryApi.confirmDirective(id),
    onSuccess: (d) => {
      toast.success(`Directive ${d.kind}: ${d.status}`);
      void qc.invalidateQueries({ queryKey: ["secretary", "directives"] });
    },
    onError: (e) => toast.error(getErrorMessage(e)),
  });

  const rejectMutation = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason: string }) =>
      secretaryApi.rejectDirective(id, reason),
    onSuccess: () => {
      toast.success("Directive rejected");
      void qc.invalidateQueries({ queryKey: ["secretary", "directives"] });
    },
    onError: (e) => toast.error(getErrorMessage(e)),
  });

  const busy = confirmMutation.isPending || rejectMutation.isPending;

  const handleStart = async () => {
    setStarting(true);
    try {
      await start(input.trim() || undefined);
      setInput("");
    } catch (e) {
      toast.error(getErrorMessage(e));
    } finally {
      setStarting(false);
    }
  };

  const handleSend = async () => {
    const text = input.trim();
    if (!text) return;
    setInput("");
    try {
      await send(text);
    } catch (e) {
      toast.error(getErrorMessage(e));
    }
  };

  return (
    <div className="grid gap-6 lg:grid-cols-3">
      {/* Chat panel */}
      <Card className="flex min-h-[60vh] flex-col lg:col-span-2">
        <CardHeader className="flex-row items-center justify-between space-y-0 pb-3">
          <CardTitle>Chat</CardTitle>
          {sessionId && (
            <Button variant="outline" size="sm" onClick={() => void stop()}>
              End session
            </Button>
          )}
        </CardHeader>
        <CardContent className="flex flex-1 flex-col gap-4">
          <ChatMessages messages={messages} streaming={streaming} />
          <div className="flex gap-2">
            <Textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={
                sessionId
                  ? "Message your Secretary…"
                  : "Opening message (optional)…"
              }
              rows={2}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  if (sessionId) void handleSend();
                  else void handleStart();
                }
              }}
            />
            {sessionId ? (
              <Button
                onClick={() => void handleSend()}
                disabled={!input.trim()}
              >
                <Send className="h-4 w-4" />
              </Button>
            ) : (
              <Button onClick={() => void handleStart()} disabled={starting}>
                {starting ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  "Start"
                )}
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Pending directives panel */}
      <Card className="flex flex-col">
        <CardHeader>
          <CardTitle>Needs your confirmation</CardTitle>
        </CardHeader>
        <CardContent className="flex-1">
          {directivesLoading ? (
            <DirectivesSkeleton />
          ) : directivesError ? (
            <OfflineState
              title="Failed to load directives"
              description="Could not reach the API."
              onRetry={() => void refetchDirectives()}
            />
          ) : pending.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No directives waiting. High-impact actions the Secretary proposes
              will appear here for you to confirm or reject.
            </p>
          ) : (
            <div className="space-y-3">
              {pending.map((d: SecretaryDirective) => (
                <DirectiveCard
                  key={d.id}
                  directive={d}
                  busy={busy}
                  onConfirm={(id) => confirmMutation.mutate(id)}
                  onReject={(id, reason) =>
                    rejectMutation.mutate({ id, reason })
                  }
                />
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
