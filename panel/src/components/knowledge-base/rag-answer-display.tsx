"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { RAGQueryResponse } from "@/types";
import { RAGCitationCard } from "./rag-citation-card";
import { Markdown } from "@/components/ui/markdown";
import { Skeleton } from "@/components/ui/skeleton";
import { Bot, BookOpen, MessageSquareText, Sparkles, AlertCircle } from "lucide-react";

interface RAGAnswerDisplayProps {
  response: RAGQueryResponse | null;
  isLoading: boolean;
  question: string | null;
  error?: string | null;
}

export function RAGAnswerDisplay({ response, isLoading, question, error }: RAGAnswerDisplayProps) {
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
          <Bot className="h-12 w-12 text-muted-foreground/50" />
          <Sparkles className="h-5 w-5 text-yellow-500 absolute -top-1 -right-1" />
        </div>
        <h3 className="text-lg font-medium mb-1">Ask AI</h3>
        <p className="text-sm text-muted-foreground max-w-md">
          Ask questions about your codebase, documentation, or past conversations.
          The AI will provide answers with citations from the knowledge base.
        </p>
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
              Query Failed
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

  return (
    <div className="space-y-4">
      {/* Question */}
      <div className="flex items-start gap-3">
        <div className="w-8 h-8 rounded-full bg-muted flex items-center justify-center shrink-0">
          <MessageSquareText className="h-4 w-4" />
        </div>
        <div className="flex-1 pt-1">
          <p className="text-sm font-medium">{response.query}</p>
        </div>
      </div>

      {/* Answer */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium flex items-center gap-2">
            <Bot className="h-4 w-4 text-primary" />
            AI Answer
            <span className="text-xs text-muted-foreground font-normal ml-auto">
              {response.context_used} sources used
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="prose prose-sm dark:prose-invert max-w-none">
            <Markdown>{response.answer}</Markdown>
          </div>
        </CardContent>
      </Card>

      {/* Citations */}
      {response.citations.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-sm font-medium">
            <BookOpen className="h-4 w-4 text-muted-foreground" />
            Citations ({response.citations.length})
          </div>
          <div className="space-y-2">
            {response.citations.map((citation, index) => (
              <RAGCitationCard key={index} citation={citation} index={index} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
