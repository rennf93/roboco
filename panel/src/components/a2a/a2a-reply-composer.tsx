"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Send } from "lucide-react";
import { toast } from "sonner";
import { getAgentDisplayName } from "@/lib/agent-utils";
import { getErrorMessage } from "@/lib/api/client";
import { useReplyAsCeo } from "@/hooks/use-a2a-live";
import { pickDefaultRecipient } from "./a2a-utils";

interface A2AReplyComposerProps {
  conversationId: string;
  agentA: string;
  agentB: string;
  /** Slug of the sender of the latest transcript message (default recipient). */
  lastSender: string | null;
  disabled?: boolean;
}

export function A2AReplyComposer({
  conversationId,
  agentA,
  agentB,
  lastSender,
  disabled,
}: A2AReplyComposerProps) {
  const [content, setContent] = useState("");
  // null = follow the default (last sender) until the CEO picks explicitly.
  const [chosenRecipient, setChosenRecipient] = useState<string | null>(null);
  const reply = useReplyAsCeo();

  const recipient =
    chosenRecipient ?? pickDefaultRecipient(agentA, agentB, lastSender);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = content.trim();
    if (!trimmed || reply.isPending) return;

    reply.mutate(
      { conversationId, to_agent: recipient, content: trimmed },
      {
        onSuccess: () => {
          toast.success(`Reply sent to ${getAgentDisplayName(recipient)}`);
          setContent("");
        },
        onError: (error) => {
          toast.error(getErrorMessage(error));
        },
      },
    );
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="p-4">
      <div className="flex items-end gap-2">
        <div className="flex-1">
          <Textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Chime in... (Shift+Enter for new line)"
            className="min-h-[60px] resize-none"
            disabled={disabled || reply.isPending}
          />
        </div>
        <div className="flex flex-col gap-2">
          <Select value={recipient} onValueChange={setChosenRecipient}>
            <SelectTrigger className="w-auto min-w-32 h-8">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {[agentA, agentB].map((slug) => (
                <SelectItem key={slug} value={slug}>
                  {getAgentDisplayName(slug)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            type="submit"
            size="sm"
            disabled={!content.trim() || disabled || reply.isPending}
          >
            <Send className="h-4 w-4 mr-1" />
            Send
          </Button>
        </div>
      </div>
      <p className="text-xs text-muted-foreground mt-2">
        Sends a direct A2A message from you to the selected participant. It
        lands in your own conversation with that agent, not inside this
        transcript.
      </p>
    </form>
  );
}
