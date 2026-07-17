"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Send } from "lucide-react";
import { toast } from "sonner";
import { HelpTip } from "@/components/ui/help-tip";
import { getAgentDisplayName } from "@/lib/agent-utils";
import { getErrorMessage } from "@/lib/api/client";
import { useSendCeoMessage } from "@/hooks/use-a2a-live";

interface A2ADirectComposerProps {
  conversationId: string;
  /** The one non-CEO participant — always the implicit recipient, no picker
   * needed (unlike A2AReplyComposer, which addresses either participant of
   * a watched conversation). */
  otherAgent: string;
  disabled?: boolean;
}

/**
 * Composer for a conversation the CEO itself owns (opened via "New DM").
 * Posts through the plain per-conversation send route as "ceo" — NOT the
 * interject-as-ceo route A2AReplyComposer uses, which requires a task link
 * this kind of conversation rarely has.
 */
export function A2ADirectComposer({
  conversationId,
  otherAgent,
  disabled,
}: A2ADirectComposerProps) {
  const [content, setContent] = useState("");
  const send = useSendCeoMessage();

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = content.trim();
    if (!trimmed || send.isPending) return;

    send.mutate(
      { conversationId, content: trimmed },
      {
        onSuccess: () => setContent(""),
        onError: (error) => toast.error(getErrorMessage(error)),
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
            placeholder="Message... (Shift+Enter for new line)"
            className="min-h-[60px] resize-none"
            disabled={disabled || send.isPending}
          />
        </div>
        <HelpTip label={`Sends directly to ${getAgentDisplayName(otherAgent)}`}>
          <span>
            <Button
              type="submit"
              size="sm"
              disabled={!content.trim() || disabled || send.isPending}
            >
              <Send className="h-4 w-4 mr-1" />
              Send
            </Button>
          </span>
        </HelpTip>
      </div>
      <p className="text-xs text-muted-foreground mt-2">
        Your own direct thread with {getAgentDisplayName(otherAgent)} —
        visible only to the two of you.
      </p>
    </form>
  );
}
