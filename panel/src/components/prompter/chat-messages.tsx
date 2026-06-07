"use client";

import { useEffect, useRef } from "react";
import { AlertTriangle } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ChatMessage } from "@/hooks/use-prompter";
import { DraftProposalCard } from "./draft-proposal-card";

interface ChatMessagesProps {
  messages: ChatMessage[];
  onOpenReview: () => void;
  onKeepChatting: () => void;
}

export function ChatMessages({
  messages,
  onOpenReview,
  onKeepChatting,
}: ChatMessagesProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom whenever messages change
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 text-center text-muted-foreground px-8">
        <p className="text-lg font-semibold">What would you like to build?</p>
        <p className="text-sm max-w-md">
          Describe your task idea. I&apos;ll help you refine it into a structured task with acceptance
          criteria ready to hand off to the team.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col gap-4 overflow-y-auto px-4 py-4">
      {messages.map((msg) => {
        if (msg.role === "user") {
          return (
            <div key={msg.id} className="flex justify-end">
              <div className="max-w-[70%] rounded-2xl rounded-tr-sm bg-primary px-4 py-3 text-sm text-primary-foreground">
                {msg.content}
              </div>
            </div>
          );
        }

        if (msg.role === "error") {
          return (
            <div key={msg.id} className="flex justify-start">
              <div className="flex max-w-[70%] items-start gap-2 rounded-2xl rounded-tl-sm border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <span>{msg.content}</span>
              </div>
            </div>
          );
        }

        // Assistant message
        return (
          <div key={msg.id} className="flex flex-col gap-2">
            <div className="flex justify-start">
              <div
                className={cn(
                  "max-w-[70%] rounded-2xl rounded-tl-sm bg-muted px-4 py-3 text-sm",
                  msg.draft && "max-w-[85%]"
                )}
              >
                <p className="whitespace-pre-wrap">{msg.content}</p>
              </div>
            </div>

            {/* Inline draft proposal card when LLM offers a draft */}
            {msg.draft && (
              <div className="flex justify-start">
                <div className="w-full max-w-[85%]">
                  <DraftProposalCard
                    draft={msg.draft}
                    onKeepChatting={onKeepChatting}
                    onOpenReview={onOpenReview}
                  />
                </div>
              </div>
            )}
          </div>
        );
      })}

      {/* Scroll anchor */}
      <div ref={bottomRef} />
    </div>
  );
}
