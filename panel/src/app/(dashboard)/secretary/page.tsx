"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Check, Loader2, Send, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { getErrorMessage } from "@/lib/api/client";
import { secretaryApi, type SecretaryDirective } from "@/lib/api/secretary";
import { useSecretary } from "@/hooks/use-secretary";

function DirectiveCard({
  directive,
  onConfirm,
  onReject,
  busy,
}: {
  directive: SecretaryDirective;
  onConfirm: (id: string) => void;
  onReject: (id: string) => void;
  busy: boolean;
}) {
  return (
    <div className="space-y-2 rounded-lg border p-3">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">{directive.kind}</span>
        <span className="text-xs text-muted-foreground">{directive.status}</span>
      </div>
      <pre className="overflow-x-auto rounded bg-muted p-2 text-xs">
        {JSON.stringify(directive.payload, null, 2)}
      </pre>
      <div className="flex gap-2">
        <Button size="sm" disabled={busy} onClick={() => onConfirm(directive.id)}>
          <Check className="mr-1 h-4 w-4" /> Confirm
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={() => onReject(directive.id)}
        >
          <X className="mr-1 h-4 w-4" /> Reject
        </Button>
      </div>
    </div>
  );
}

export default function SecretaryPage() {
  const qc = useQueryClient();
  const { sessionId, messages, streaming, start, send, stop } = useSecretary();
  const [input, setInput] = useState("");
  const [starting, setStarting] = useState(false);

  const { data: pending = [] } = useQuery({
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
    mutationFn: (id: string) => secretaryApi.rejectDirective(id),
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
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Secretary</h1>
          <p className="text-muted-foreground">
            Your chief-of-staff. It acts only on your command; high-impact
            actions wait for your confirmation on the right.
          </p>
        </div>
        {sessionId && (
          <Button variant="outline" onClick={() => void stop()}>
            End session
          </Button>
        )}
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="flex min-h-[60vh] flex-col lg:col-span-2">
          <CardHeader>
            <CardTitle>Chat</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-1 flex-col gap-4">
            <div className="flex-1 space-y-3 overflow-y-auto">
              {messages.length === 0 && (
                <p className="text-sm text-muted-foreground">
                  {sessionId
                    ? "Say something to your Secretary…"
                    : "Start a session to talk to your Secretary."}
                </p>
              )}
              {messages.map((m, i) => (
                <div
                  key={i}
                  className={
                    m.role === "user"
                      ? "ml-auto max-w-[80%] rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground"
                      : "mr-auto max-w-[80%] whitespace-pre-wrap rounded-lg bg-muted px-3 py-2 text-sm"
                  }
                >
                  {m.text}
                </div>
              ))}
              {streaming && (
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <Loader2 className="h-3 w-3 animate-spin" /> thinking…
                </div>
              )}
            </div>
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
                <Button onClick={() => void handleSend()} disabled={!input.trim()}>
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

        <Card className="flex flex-col">
          <CardHeader>
            <CardTitle>Needs your confirmation</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {pending.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No directives waiting. High-impact actions the Secretary proposes
                will appear here for you to confirm or reject.
              </p>
            ) : (
              pending.map((d) => (
                <DirectiveCard
                  key={d.id}
                  directive={d}
                  busy={busy}
                  onConfirm={(id) => confirmMutation.mutate(id)}
                  onReject={(id) => rejectMutation.mutate(id)}
                />
              ))
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
