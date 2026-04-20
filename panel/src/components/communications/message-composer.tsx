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

interface MessageComposerProps {
  channelId: string;
  onSend: (message: { content: string; type: string }) => void;
  isSending?: boolean;
  disabled?: boolean;
}

const MESSAGE_TYPES = [
  { value: "dialogue", label: "Dialogue" },
  { value: "reasoning", label: "Reasoning" },
  { value: "decision", label: "Decision" },
  { value: "action", label: "Action" },
  { value: "blocker", label: "Blocker" },
  { value: "technical", label: "Technical" },
];

export function MessageComposer({
  onSend,
  isSending,
  disabled,
}: MessageComposerProps) {
  const [content, setContent] = useState("");
  const [type, setType] = useState("dialogue");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!content.trim()) return;

    onSend({ content: content.trim(), type });
    setContent("");
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="border-t p-4">
      <div className="flex items-end gap-2">
        <div className="flex-1">
          <Textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Type a message... (Shift+Enter for new line)"
            className="min-h-[60px] resize-none"
            disabled={disabled || isSending}
          />
        </div>
        <div className="flex flex-col gap-2">
          <Select value={type} onValueChange={setType}>
            <SelectTrigger className="w-auto min-w-24 h-8">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {MESSAGE_TYPES.map((t) => (
                <SelectItem key={t.value} value={t.value}>
                  {t.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            type="submit"
            size="sm"
            disabled={!content.trim() || disabled || isSending}
          >
            <Send className="h-4 w-4 mr-1" />
            Send
          </Button>
        </div>
      </div>
      <p className="text-xs text-muted-foreground mt-2">
        Markdown supported. Use @agent to mention.
      </p>
    </form>
  );
}
