"use client";

import { useRef, useEffect, KeyboardEvent } from "react";
import { Send, Bot, User } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import { ModelSelector, DEFAULT_MODEL } from "./model-selector";
import { cn } from "@/lib/utils";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: string;
}

/**
 * A structured draft field extracted from an AI response.
 * This is a partial draft — fields populate progressively as the conversation continues.
 */
export interface DraftUpdate {
  title?: string;
  description?: string;
  acceptanceCriteria?: string[];
  team?: string;
  priority?: string;
}

interface ChatPanelProps {
  messages: ChatMessage[];
  inputValue: string;
  selectedModel: string;
  isGenerating: boolean;
  onInputChange: (value: string) => void;
  onSend: () => void;
  onModelChange: (model: string) => void;
}

/** Returns the time portion of an ISO timestamp in HH:MM format */
function formatTime(isoTimestamp: string): string {
  return new Date(isoTimestamp).toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";

  return (
    <div
      className={cn(
        "flex items-start gap-2.5",
        isUser && "flex-row-reverse"
      )}
      role="article"
      aria-label={`${isUser ? "Your" : "Assistant"} message`}
    >
      {/* Avatar icon */}
      <div
        className={cn(
          "flex-shrink-0 h-7 w-7 rounded-full flex items-center justify-center mt-0.5",
          isUser
            ? "bg-primary text-primary-foreground"
            : "bg-muted text-muted-foreground border border-border"
        )}
        aria-hidden="true"
      >
        {isUser ? (
          <User className="h-3.5 w-3.5" />
        ) : (
          <Bot className="h-3.5 w-3.5" />
        )}
      </div>

      {/* Bubble */}
      <div
        className={cn(
          "max-w-[80%] rounded-2xl px-4 py-2.5 text-sm",
          isUser
            ? "bg-primary text-primary-foreground rounded-tr-sm"
            : "bg-muted text-foreground rounded-tl-sm border border-border"
        )}
      >
        <p className="leading-relaxed whitespace-pre-wrap">{message.content}</p>
        <p
          className={cn(
            "text-[10px] mt-1",
            isUser ? "text-primary-foreground/60 text-right" : "text-muted-foreground"
          )}
          aria-label={`Sent at ${formatTime(message.timestamp)}`}
        >
          {formatTime(message.timestamp)}
        </p>
      </div>
    </div>
  );
}

function TypingIndicator() {
  return (
    <div className="flex items-start gap-2.5" role="status" aria-live="polite" aria-label="Assistant is typing">
      <div className="flex-shrink-0 h-7 w-7 rounded-full flex items-center justify-center bg-muted border border-border" aria-hidden="true">
        <Bot className="h-3.5 w-3.5 text-muted-foreground" />
      </div>
      <div className="bg-muted rounded-2xl rounded-tl-sm border border-border px-4 py-3">
        <div className="flex items-center gap-1" aria-hidden="true">
          <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground animate-bounce [animation-delay:0ms]" />
          <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground animate-bounce [animation-delay:150ms]" />
          <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground animate-bounce [animation-delay:300ms]" />
        </div>
      </div>
    </div>
  );
}

export function ChatPanel({
  messages,
  inputValue,
  selectedModel,
  isGenerating,
  onInputChange,
  onSend,
  onModelChange,
}: ChatPanelProps) {
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-scroll to bottom when new messages arrive by scrolling the sentinel div into view
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isGenerating]);

  // Submit on Cmd/Ctrl+Enter
  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      if (inputValue.trim() && !isGenerating) {
        onSend();
      }
    }
  }

  return (
    <Card className="h-full flex flex-col gap-0 py-0 overflow-hidden">
      {/*
       * CHAT HEADER — model selector placement rationale is in model-selector.tsx.
       * The header also shows a "generating" badge so users know the model is working.
       */}
      <div className="flex items-center justify-between px-4 py-3 border-b shrink-0">
        <div className="flex items-center gap-3">
          <span className="text-sm font-medium">Chat</span>
          {isGenerating && (
            <Badge variant="secondary" className="text-xs animate-pulse">
              Generating…
            </Badge>
          )}
        </div>
        {/*
         * MODEL SELECTOR in the chat header.
         * See model-selector.tsx for the full rationale comment explaining
         * why this placement minimises workflow disruption.
         */}
        <ModelSelector
          value={selectedModel || DEFAULT_MODEL}
          onValueChange={onModelChange}
        />
      </div>

      {/* Message list */}
      <ScrollArea className="flex-1 px-4 py-4">
        <div className="space-y-4" role="log" aria-label="Chat messages" aria-live="polite">
          {messages.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-center text-muted-foreground">
              <Bot className="h-10 w-10 mb-3 opacity-30" aria-hidden="true" />
              <p className="text-sm font-medium">Start a conversation</p>
              <p className="text-xs mt-1 max-w-[220px]">
                Describe the task you want to create. The AI will help you structure it into a complete task draft.
              </p>
            </div>
          ) : (
            messages.map((msg) => (
              <MessageBubble key={msg.id} message={msg} />
            ))
          )}
          {isGenerating && <TypingIndicator />}
          {/* Sentinel div — scrolled into view whenever messages update */}
          <div ref={messagesEndRef} aria-hidden="true" />
        </div>
      </ScrollArea>

      {/* Input area */}
      <div className="border-t px-4 py-3 shrink-0">
        <div className="flex items-end gap-2">
          <Textarea
            ref={textareaRef}
            value={inputValue}
            onChange={(e) => onInputChange(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Describe the task you want to create… (⌘+Enter to send)"
            className="min-h-[64px] max-h-[160px] resize-none flex-1 text-sm"
            disabled={isGenerating}
            aria-label="Chat message input"
          />
          <Button
            size="icon"
            onClick={onSend}
            disabled={!inputValue.trim() || isGenerating}
            className="h-10 w-10 shrink-0"
            aria-label="Send message"
          >
            <Send className="h-4 w-4" aria-hidden="true" />
          </Button>
        </div>
        <p className="text-[10px] text-muted-foreground mt-1.5 text-right">
          ⌘+Enter to send
        </p>
      </div>
    </Card>
  );
}
