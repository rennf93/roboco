"use client";

import { useState, useRef, KeyboardEvent } from "react";
import { Send, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

interface ChatComposerProps {
  onSend: (text: string) => Promise<void> | void;
  disabled?: boolean;
  isSending?: boolean;
  placeholder?: string;
}

export function ChatComposer({
  onSend,
  disabled = false,
  isSending = false,
  placeholder = "Describe the task you want to create… (Enter to send, Shift+Enter for newline)",
}: ChatComposerProps) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const isDisabled = disabled || isSending || !value.trim();

  const handleSend = async () => {
    const text = value.trim();
    if (!text || isSending || disabled) return;
    setValue("");
    await onSend(text);
    // Refocus after send
    textareaRef.current?.focus();
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
    // Shift+Enter falls through to default (inserts newline)
  };

  return (
    <div className="flex items-end gap-2 border-t bg-background px-4 py-3">
      <Textarea
        ref={textareaRef}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        disabled={disabled || isSending}
        rows={2}
        className="flex-1 resize-none text-sm"
      />
      <Button
        size="icon"
        onClick={handleSend}
        disabled={isDisabled}
        className="mb-0.5 shrink-0"
        aria-label="Send message"
      >
        {isSending ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Send className="h-4 w-4" />
        )}
      </Button>
    </div>
  );
}
