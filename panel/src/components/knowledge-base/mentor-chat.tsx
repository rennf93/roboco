"use client";

import { useState, useRef, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Markdown } from "@/components/ui/markdown";
import {
  Send,
  Loader2,
  Brain,
  User,
  BookMarked,
  MessageCircle,
  RotateCcw,
  Sparkles,
} from "lucide-react";
import { MentorAskResponse, KBSearchResult } from "@/types";
import { RAGCitationCard } from "./rag-citation-card";

interface ChatMessage {
  role: "user" | "mentor";
  content: string;
  sources?: KBSearchResult[];
  followups?: string[];
  agentRole?: string;
  agentTeam?: string;
  journalEntriesUsed?: number;
}

interface MentorChatProps {
  onAsk: (question: string, conversationId?: string) => Promise<MentorAskResponse>;
  isLoading: boolean;
}

export function MentorChat({ onAsk, isLoading }: MentorChatProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [expandedSources, setExpandedSources] = useState<number | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, isLoading]);

  const handleSubmit = async (question?: string) => {
    const q = question || input.trim();
    if (!q || isLoading) return;

    // Add user message
    setMessages((prev) => [...prev, { role: "user", content: q }]);
    setInput("");

    try {
      const response = await onAsk(q, conversationId ?? undefined);

      // Save conversation ID for follow-ups
      setConversationId(response.conversation_id);

      // Add mentor response
      setMessages((prev) => [
        ...prev,
        {
          role: "mentor",
          content: response.answer,
          sources: response.sources,
          followups: response.suggested_followups,
          agentRole: response.agent_role,
          agentTeam: response.agent_team,
          journalEntriesUsed: response.journal_entries_used,
        },
      ]);
    } catch {
      // Error handled by parent
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const handleNewChat = () => {
    setMessages([]);
    setConversationId(null);
    setExpandedSources(null);
    inputRef.current?.focus();
  };

  const handleFollowUp = (question: string) => {
    handleSubmit(question);
  };

  // Empty state
  if (messages.length === 0 && !isLoading) {
    return (
      <div className="flex flex-col h-[calc(100vh-280px)]">
        {/* Empty state */}
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center max-w-md">
            <div className="relative inline-block mb-4">
              <Brain className="h-16 w-16 text-primary/50" />
              <Sparkles className="h-6 w-6 text-yellow-500 absolute -top-1 -right-1" />
            </div>
            <h2 className="text-xl font-semibold mb-2">AI Mentor</h2>
            <p className="text-muted-foreground mb-4">
              Your personalized mentor that knows your role, searches your journals,
              and provides tailored guidance. Start a conversation below.
            </p>
            <div className="flex justify-center gap-2 flex-wrap">
              <Badge variant="secondary" className="gap-1">
                <User className="h-3 w-3" /> Role-aware
              </Badge>
              <Badge variant="secondary" className="gap-1">
                <BookMarked className="h-3 w-3" /> Personal context
              </Badge>
              <Badge variant="secondary" className="gap-1">
                <MessageCircle className="h-3 w-3" /> Multi-turn chat
              </Badge>
            </div>
          </div>
        </div>

        {/* Input at bottom */}
        <div className="border-t pt-4">
          <div className="relative">
            <Textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask your mentor a question..."
              className="min-h-20 pr-12 resize-none"
              disabled={isLoading}
            />
            <Button
              size="icon"
              className="absolute bottom-2 right-2"
              onClick={() => handleSubmit()}
              disabled={!input.trim() || isLoading}
            >
              <Send className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-[calc(100vh-280px)]">
      {/* Header with New Chat */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Brain className="h-5 w-5 text-primary" />
          <span className="font-medium">Mentor Chat</span>
          {conversationId && (
            <Badge variant="outline" className="text-xs">
              Conversation active
            </Badge>
          )}
        </div>
        <Button variant="ghost" size="sm" onClick={handleNewChat}>
          <RotateCcw className="h-4 w-4 mr-1" />
          New Chat
        </Button>
      </div>

      {/* Messages */}
      <ScrollArea className="flex-1 pr-4" ref={scrollRef}>
        <div className="space-y-4">
          {messages.map((msg, idx) => (
            <div key={idx}>
              {msg.role === "user" ? (
                // User message
                <div className="flex justify-end">
                  <div className="bg-primary text-primary-foreground rounded-lg px-4 py-2 max-w-[80%]">
                    <p className="text-sm">{msg.content}</p>
                  </div>
                </div>
              ) : (
                // Mentor message
                <div className="space-y-3">
                  {/* Personalization badge */}
                  {(msg.agentRole || msg.journalEntriesUsed) && (
                    <div className="flex items-center gap-2 text-xs text-muted-foreground">
                      <Brain className="h-3 w-3" />
                      {msg.agentRole && (
                        <span>
                          Role: <Badge variant="secondary" className="text-xs">{msg.agentRole}</Badge>
                          {msg.agentTeam && <span className="ml-1">({msg.agentTeam})</span>}
                        </span>
                      )}
                      {msg.journalEntriesUsed !== undefined && msg.journalEntriesUsed > 0 && (
                        <span className="flex items-center gap-1">
                          <BookMarked className="h-3 w-3" />
                          {msg.journalEntriesUsed} journal{msg.journalEntriesUsed !== 1 ? "s" : ""} used
                        </span>
                      )}
                    </div>
                  )}

                  {/* Answer */}
                  <Card>
                    <CardContent className="pt-4">
                      <div className="prose prose-sm dark:prose-invert max-w-none">
                        <Markdown>{msg.content}</Markdown>
                      </div>
                    </CardContent>
                  </Card>

                  {/* Sources toggle */}
                  {msg.sources && msg.sources.length > 0 && (
                    <div>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-xs"
                        onClick={() => setExpandedSources(expandedSources === idx ? null : idx)}
                      >
                        {expandedSources === idx ? "Hide" : "Show"} {msg.sources.length} sources
                      </Button>
                      {expandedSources === idx && (
                        <div className="mt-2 space-y-2">
                          {msg.sources.map((source, sidx) => (
                            <RAGCitationCard
                              key={sidx}
                              citation={{
                                content: source.content,
                                source: source.source,
                                score: source.score,
                                index_type: source.index_type,
                                metadata: source.metadata,
                              }}
                              index={sidx}
                            />
                          ))}
                        </div>
                      )}
                    </div>
                  )}

                  {/* Follow-ups */}
                  {msg.followups && msg.followups.length > 0 && (
                    <div className="flex flex-wrap gap-2">
                      {msg.followups.map((followup, fidx) => (
                        <Button
                          key={fidx}
                          variant="outline"
                          size="sm"
                          className="text-xs h-auto py-1.5"
                          onClick={() => handleFollowUp(followup)}
                          disabled={isLoading}
                        >
                          {followup}
                        </Button>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}

          {/* Loading indicator */}
          {isLoading && (
            <div className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              <span className="text-sm">Mentor is thinking...</span>
            </div>
          )}
        </div>
      </ScrollArea>

      {/* Input at bottom */}
      <div className="border-t pt-4 mt-4">
        <div className="relative">
          <Textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Continue the conversation..."
            className="min-h-16 pr-12 resize-none"
            disabled={isLoading}
          />
          <Button
            size="icon"
            className="absolute bottom-2 right-2"
            onClick={() => handleSubmit()}
            disabled={!input.trim() || isLoading}
          >
            {isLoading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Send className="h-4 w-4" />
            )}
          </Button>
        </div>
      </div>
    </div>
  );
}
