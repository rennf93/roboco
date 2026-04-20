"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { MentorAskResponse } from "@/types";
import { RAGCitationCard } from "./rag-citation-card";
import { Markdown } from "@/components/ui/markdown";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Brain,
  BookOpen,
  MessageSquareText,
  Sparkles,
  AlertCircle,
  Lightbulb,
  BarChart3,
  MessageCircleQuestion,
  User,
  BookMarked,
} from "lucide-react";

interface MentorAnswerDisplayProps {
  response: MentorAskResponse | null;
  isLoading: boolean;
  question: string | null;
  error?: string | null;
  onFollowUp?: (question: string) => void;
}

export function MentorAnswerDisplay({
  response,
  isLoading,
  question,
  error,
  onFollowUp,
}: MentorAnswerDisplayProps) {
  if (isLoading) {
    return (
      <div className="space-y-4">
        <Card>
          <CardHeader className="pb-2">
            <div className="flex items-center gap-2">
              <Skeleton className="h-5 w-5 rounded-full" />
              <Skeleton className="h-5 w-32" />
            </div>
          </CardHeader>
          <CardContent className="space-y-2">
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-5/6" />
          </CardContent>
        </Card>
        <div className="space-y-2">
          <Skeleton className="h-4 w-24" />
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      </div>
    );
  }

  if (!response && !question && !error) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-center">
        <div className="relative mb-4">
          <Brain className="h-12 w-12 text-muted-foreground/50" />
          <Sparkles className="h-5 w-5 text-yellow-500 absolute -top-1 -right-1" />
        </div>
        <h3 className="text-lg font-medium mb-1">AI Mentor</h3>
        <p className="text-sm text-muted-foreground max-w-md">
          Your personalized AI mentor that knows your role and past experiences.
          Ask questions about standards, workflows, or get guidance on your tasks.
        </p>
        <div className="flex gap-2 mt-4 flex-wrap justify-center">
          <Badge variant="outline" className="text-xs">Role-aware</Badge>
          <Badge variant="outline" className="text-xs">Personal context</Badge>
          <Badge variant="outline" className="text-xs">Follow-ups</Badge>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="space-y-4">
        {question && (
          <div className="flex items-start gap-3">
            <div className="w-8 h-8 rounded-full bg-muted flex items-center justify-center shrink-0">
              <MessageSquareText className="h-4 w-4" />
            </div>
            <div className="flex-1 pt-1">
              <p className="text-sm font-medium">{question}</p>
            </div>
          </div>
        )}
        <Card className="border-red-500/50 bg-red-50 dark:bg-red-950/20">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium flex items-center gap-2 text-red-600 dark:text-red-400">
              <AlertCircle className="h-4 w-4" />
              Mentor Query Failed
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (!response) {
    return null;
  }

  // Calculate total sources searched
  const totalSearched = response.search_stats
    ? Object.values(response.search_stats).reduce((sum, count) => sum + (count > 0 ? count : 0), 0)
    : 0;

  return (
    <div className="space-y-4">
      {/* Question */}
      <div className="flex items-start gap-3">
        <div className="w-8 h-8 rounded-full bg-muted flex items-center justify-center shrink-0">
          <MessageSquareText className="h-4 w-4" />
        </div>
        <div className="flex-1 pt-1">
          <p className="text-sm font-medium">{question}</p>
        </div>
      </div>

      {/* Personalization Context - What makes this different from Ask AI */}
      {(response.agent_role || response.journal_entries_used) && (
        <div className="flex items-center gap-3 p-3 bg-primary/5 rounded-lg border border-primary/20">
          <Brain className="h-5 w-5 text-primary shrink-0" />
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
            {response.agent_role && (
              <span className="flex items-center gap-1.5">
                <User className="h-3.5 w-3.5 text-muted-foreground" />
                <span className="text-muted-foreground">Role:</span>
                <Badge variant="secondary" className="font-medium">
                  {response.agent_role}
                </Badge>
                {response.agent_team && (
                  <span className="text-muted-foreground">({response.agent_team})</span>
                )}
              </span>
            )}
            {response.journal_entries_used !== undefined && response.journal_entries_used > 0 && (
              <span className="flex items-center gap-1.5">
                <BookMarked className="h-3.5 w-3.5 text-muted-foreground" />
                <span className="text-muted-foreground">
                  {response.journal_entries_used} personal journal{response.journal_entries_used !== 1 ? "s" : ""} used
                </span>
              </span>
            )}
          </div>
        </div>
      )}

      {/* Answer */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium flex items-center gap-2">
            <Brain className="h-4 w-4 text-primary" />
            Mentor Response
            <span className="text-xs text-muted-foreground font-normal ml-auto">
              {response.sources.length} sources used
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="prose prose-sm dark:prose-invert max-w-none">
            <Markdown>{response.answer}</Markdown>
          </div>
        </CardContent>
      </Card>

      {/* Suggested Follow-ups */}
      {response.suggested_followups && response.suggested_followups.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-sm font-medium">
            <Lightbulb className="h-4 w-4 text-yellow-500" />
            Follow-up Questions
          </div>
          <div className="flex flex-wrap gap-2">
            {response.suggested_followups.map((followup, index) => (
              <Button
                key={index}
                variant="outline"
                size="sm"
                className="text-xs h-auto py-1.5 px-3"
                onClick={() => onFollowUp?.(followup)}
              >
                <MessageCircleQuestion className="h-3 w-3 mr-1.5" />
                {followup}
              </Button>
            ))}
          </div>
        </div>
      )}

      {/* Search Stats */}
      {response.search_stats && Object.keys(response.search_stats).length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-sm font-medium">
            <BarChart3 className="h-4 w-4 text-muted-foreground" />
            Search Stats
            <span className="text-xs text-muted-foreground font-normal">
              ({totalSearched} total results)
            </span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(response.search_stats).map(([indexType, count]) => (
              <Badge
                key={indexType}
                variant={count > 0 ? "secondary" : "outline"}
                className={`text-xs ${count === -1 ? "text-red-500" : ""}`}
              >
                {indexType}: {count === -1 ? "error" : count}
              </Badge>
            ))}
          </div>
        </div>
      )}

      {/* Citations */}
      {response.sources.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-sm font-medium">
            <BookOpen className="h-4 w-4 text-muted-foreground" />
            Sources ({response.sources.length})
          </div>
          <div className="space-y-2">
            {response.sources.map((source, index) => (
              <RAGCitationCard
                key={index}
                citation={{
                  content: source.content,
                  source: source.source,
                  score: source.score,
                  index_type: source.index_type,
                  metadata: source.metadata,
                }}
                index={index}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
