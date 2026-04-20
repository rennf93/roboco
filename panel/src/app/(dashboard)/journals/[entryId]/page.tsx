"use client";

import { use } from "react";
import { useJournalEntry } from "@/hooks/use-journals";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Markdown } from "@/components/ui/markdown";
import { EntryTypeBadge } from "@/components/journals/entry-type-badge";
import {
  ArrowLeft,
  AlertTriangle,
  RefreshCw,
  Clock,
  Tag,
  Link2,
  User,
} from "lucide-react";
import Link from "next/link";

interface JournalEntryPageProps {
  params: Promise<{ entryId: string }>;
}

function formatFullDate(timestamp: string): string {
  const date = new Date(timestamp);
  return date.toLocaleDateString("en-US", {
    weekday: "long",
    year: "numeric",
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function JournalEntryPage({ params }: JournalEntryPageProps) {
  const { entryId } = use(params);
  const { data: entry, isLoading, error, refetch } = useJournalEntry(entryId);

  // Loading state
  if (isLoading) {
    return (
      <div className="space-y-6">
        <div className="flex items-center gap-4">
          <Skeleton className="h-10 w-10" />
          <div className="space-y-2">
            <Skeleton className="h-8 w-64" />
            <Skeleton className="h-4 w-32" />
          </div>
        </div>
        <Skeleton className="h-48" />
        <Skeleton className="h-96" />
      </div>
    );
  }

  // Error state
  if (error || !entry) {
    return (
      <div className="space-y-6">
        <Link href="/journals">
          <Button variant="ghost" size="sm">
            <ArrowLeft className="h-4 w-4 mr-2" />
            Back to Journals
          </Button>
        </Link>

        <Card>
          <CardContent className="pt-6">
            <div className="text-center py-12">
              <AlertTriangle className="h-16 w-16 mx-auto mb-4 text-destructive" />
              <h2 className="text-xl font-semibold mb-2">Entry Not Found</h2>
              <p className="text-muted-foreground mb-6">
                {error?.message ??
                  "The journal entry you're looking for doesn't exist or has been deleted."}
              </p>
              <div className="flex justify-center gap-4">
                <Button variant="outline" onClick={() => refetch()}>
                  <RefreshCw className="h-4 w-4 mr-2" />
                  Retry
                </Button>
                <Link href="/journals">
                  <Button>View All Journals</Button>
                </Link>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between border-b pb-4">
        <div className="flex items-center gap-4">
          <Link href="/journals">
            <Button variant="ghost" size="icon">
              <ArrowLeft className="h-5 w-5" />
            </Button>
          </Link>
          <div>
            <div className="flex items-center gap-2 mb-1">
              <EntryTypeBadge type={entry.type} />
              {entry.sentiment && (
                <Badge variant="outline">{entry.sentiment}</Badge>
              )}
            </div>
            <h1 className="text-2xl font-bold">{entry.title}</h1>
          </div>
        </div>
      </div>

      {/* Metadata */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card>
          <CardContent className="pt-4">
            <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">
              <Clock className="h-4 w-4" />
              <span>Created</span>
            </div>
            <p className="font-medium">{formatFullDate(entry.timestamp)}</p>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="pt-4">
            <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">
              <User className="h-4 w-4" />
              <span>Journal</span>
            </div>
            <p className="font-medium">{entry.journal_id.slice(0, 8)}...</p>
          </CardContent>
        </Card>

        {entry.task_id && (
          <Card>
            <CardContent className="pt-4">
              <div className="flex items-center gap-2 text-sm text-muted-foreground mb-1">
                <Link2 className="h-4 w-4" />
                <span>Related Task</span>
              </div>
              <Link href={`/tasks/${entry.task_id}`}>
                <Badge variant="outline" className="hover:bg-muted cursor-pointer">
                  Task #{entry.task_id.slice(0, 8)}
                </Badge>
              </Link>
            </CardContent>
          </Card>
        )}
      </div>

      {/* Content */}
      <Card>
        <CardHeader>
          <CardTitle>Content</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="prose prose-sm dark:prose-invert max-w-none">
            <Markdown>{entry.content}</Markdown>
          </div>
        </CardContent>
      </Card>

      {/* Tags */}
      {entry.tags.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Tag className="h-4 w-4" />
              Tags
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-2">
              {entry.tags.map((tag) => (
                <Badge key={tag} variant="secondary">
                  {tag}
                </Badge>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
