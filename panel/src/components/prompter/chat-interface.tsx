"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ChatMessage } from "./chat-message";
import { TypingIndicator } from "./typing-indicator";
import { usePrompterStore } from "@/store/prompter-store";
import { prompterApi } from "@/lib/api/prompter";
import type { DraftFieldKey } from "@/store/prompter-store";

interface ChatInterfaceProps {
  conversationId: string;
}

export function ChatInterface({ conversationId }: ChatInterfaceProps) {
  const [inputValue, setInputValue] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const conv = usePrompterStore((s) => s.conversations[conversationId]);
  const streamingStatus = usePrompterStore((s) => s.streamingStatus);
  const selectedModel = usePrompterStore((s) => s.selectedModel);
  const addUserMessage = usePrompterStore((s) => s.addUserMessage);
  const appendAssistantToken = usePrompterStore((s) => s.appendAssistantToken);
  const finalizeAssistantMessage = usePrompterStore((s) => s.finalizeAssistantMessage);
  const setFieldFromLLM = usePrompterStore((s) => s.setFieldFromLLM);
  const setStreamingStatus = usePrompterStore((s) => s.setStreamingStatus);

  const isStreaming = streamingStatus === "streaming";

  // Scroll to bottom when messages change
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [conv?.messages.length, streamingStatus]);

  // Auto-trigger streaming when the conversation is first opened with a pending user message
  // (e.g., from clicking an example prompt in the zero-state)
  const autoTriggeredRef = useRef(false);
  useEffect(() => {
    if (autoTriggeredRef.current) return;
    if (!conv) return;
    const msgs = conv.messages;
    if (
      msgs.length === 1 &&
      msgs[0].role === "user" &&
      streamingStatus === "idle"
    ) {
      autoTriggeredRef.current = true;
      const lastUserMsg = msgs[0].content;
      // We call the stream directly without addUserMessage (already added)
      setStreamingStatus("streaming");
      const doStream = async () => {
        try {
          const stream = prompterApi.streamChat({
            message: lastUserMsg,
            model: selectedModel,
            conversationId,
            history: [],
          });
          for await (const event of stream) {
            if (event.type === "token" && event.content) {
              appendAssistantToken(event.content);
            } else if (event.type === "draft_update" && event.field) {
              setFieldFromLLM(event.field as DraftFieldKey, event.value);
            } else if (event.type === "done") {
              break;
            } else if (event.type === "error") {
              appendAssistantToken("\n\n_(Error: " + (event.message ?? "unknown") + ")_");
              break;
            }
          }
        } catch (err) {
          console.error("[Prompter] Auto-stream failed:", err);
          appendAssistantToken("\n\n_(Connection error — please try again)_");
        } finally {
          finalizeAssistantMessage();
          setStreamingStatus("done");
        }
      };
      doStream();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId]);

  const sendMessage = useCallback(
    async (content: string) => {
      if (!content.trim() || isStreaming) return;

      addUserMessage(content);
      setInputValue("");
      setStreamingStatus("streaming");

      try {
        const history = (conv?.messages ?? []).map((m) => ({
          role: m.role,
          content: m.content,
        }));

        const stream = prompterApi.streamChat({
          message: content,
          model: selectedModel,
          conversationId,
          history,
        });

        for await (const event of stream) {
          if (event.type === "token" && event.content) {
            appendAssistantToken(event.content);
          } else if (event.type === "draft_update" && event.field) {
            setFieldFromLLM(event.field as DraftFieldKey, event.value);
          } else if (event.type === "done") {
            break;
          } else if (event.type === "error") {
            console.error("[Prompter] Stream error:", event.message);
            appendAssistantToken(
              "\n\n_(Error: " + (event.message ?? "unknown") + ")_"
            );
            break;
          }
        }
      } catch (err) {
        console.error("[Prompter] Stream failed:", err);
        appendAssistantToken("\n\n_(Connection error — please try again)_");
      } finally {
        finalizeAssistantMessage();
        setStreamingStatus("done");
      }
    },
    [
      isStreaming,
      addUserMessage,
      setStreamingStatus,
      conv?.messages,
      selectedModel,
      conversationId,
      appendAssistantToken,
      setFieldFromLLM,
      finalizeAssistantMessage,
    ]
  );

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage(inputValue);
    }
  };

  if (!conv) return null;

  return (
    <div className="flex flex-col h-full">
      {/* Messages */}
      <div className="flex-1 overflow-auto px-4 py-4 space-y-4">
        {conv.messages.map((msg) => (
          <ChatMessage key={msg.id} message={msg} />
        ))}
        {isStreaming && <TypingIndicator />}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="border-t p-4 flex gap-2 items-end">
        <Textarea
          ref={textareaRef}
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Describe what you need built… (Enter to send, Shift+Enter for newline)"
          rows={3}
          disabled={isStreaming}
          className="resize-none flex-1"
        />
        <Button
          onClick={() => sendMessage(inputValue)}
          disabled={!inputValue.trim() || isStreaming}
          size="icon"
          className="shrink-0 h-10 w-10"
        >
          <Send className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}
