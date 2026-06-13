"use client";

import { useEffect, useRef } from "react";
import type { ComponentPropsWithoutRef, ReactElement, ReactNode } from "react";
import { AlertTriangle } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";
import { CopyButton } from "@/components/ui/copy-button";
import type { ChatMessage, StartRoute } from "@/hooks/use-prompter";
import { DraftProposalCard } from "./draft-proposal-card";

interface ChatMessagesProps {
  messages: ChatMessage[];
  onStart: (route: StartRoute) => void;
  onKeepChatting: () => void;
  isLaunching?: boolean;
}

/** Raw text of a fenced code block — the <pre>'s <code> child's string content. */
function codeText(children: ReactNode): string {
  const codeEl = children as ReactElement<{ children?: ReactNode }> | undefined;
  const inner = codeEl?.props?.children;
  if (typeof inner === "string") return inner;
  if (Array.isArray(inner)) {
    return inner.filter((c): c is string => typeof c === "string").join("");
  }
  return "";
}

// Copy lives on KEY PARTS only: fenced code blocks here, and the draft card has
// its own. (Not a blanket button on every whole message.)
const markdownComponents = {
  pre(props: ComponentPropsWithoutRef<"pre">) {
    const text = codeText(props.children).replace(/\n$/, "");
    return (
      <div className="group relative">
        <pre {...props} />
        {text && (
          <CopyButton
            value={text}
            className="absolute right-1.5 top-1.5 bg-background/80 opacity-0 transition-opacity group-hover:opacity-100"
          />
        )}
      </div>
    );
  },
};

/** GFM markdown that inherits the bubble's text color, so it renders correctly on
 *  both the muted assistant bubble and the primary user bubble (lists, code,
 *  newlines all preserved). */
function MarkdownBody({ content }: { content: string }) {
  return (
    <div className="prose prose-sm max-w-none !text-inherit [&_*]:!text-inherit prose-p:my-1.5 prose-headings:mt-3 prose-headings:mb-1 prose-pre:my-2 prose-pre:bg-black/20">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
        {content}
      </ReactMarkdown>
    </div>
  );
}

export function ChatMessages({
  messages,
  onStart,
  onKeepChatting,
  isLaunching,
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
              <div className="group relative max-w-[70%] rounded-2xl rounded-tr-sm bg-primary px-4 py-3 text-sm text-primary-foreground">
                <MarkdownBody content={msg.content} />
                <CopyButton
                  value={msg.content}
                  className="absolute right-1.5 top-1.5 bg-primary-foreground/10 text-primary-foreground opacity-0 transition-opacity group-hover:opacity-100"
                />
              </div>
            </div>
          );
        }

        if (msg.role === "error") {
          return (
            <div key={msg.id} className="flex justify-start">
              <div className="group relative flex max-w-[70%] items-start gap-2 rounded-2xl rounded-tl-sm border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <span>{msg.content}</span>
                <CopyButton
                  value={msg.content}
                  className="absolute right-1.5 top-1.5 bg-background/80 opacity-0 transition-opacity group-hover:opacity-100"
                />
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
                  "group relative max-w-[70%] rounded-2xl rounded-tl-sm bg-muted px-4 py-3 text-sm text-foreground",
                  msg.draft && "max-w-[85%]"
                )}
              >
                <MarkdownBody content={msg.content} />
                <CopyButton
                  value={msg.content}
                  className="absolute right-1.5 top-1.5 bg-background/80 opacity-0 transition-opacity group-hover:opacity-100"
                />
              </div>
            </div>

            {/* Inline draft proposal card when LLM offers a draft */}
            {msg.draft && (
              <div className="flex justify-start">
                <div className="w-full max-w-[85%]">
                  <DraftProposalCard
                    draft={msg.draft}
                    onKeepChatting={onKeepChatting}
                    onStart={onStart}
                    isLaunching={isLaunching}
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
