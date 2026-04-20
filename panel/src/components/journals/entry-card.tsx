"use client";

import { JournalEntry } from "@/types";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Markdown } from "@/components/ui/markdown";
import { EntryTypeBadge } from "./entry-type-badge";
import { Clock, Tag, Link2, ChevronRight } from "lucide-react";
import Link from "next/link";

interface EntryCardProps {
  entry: JournalEntry;
}

function formatTime(timestamp: string): string {
  const date = new Date(timestamp);
  return date.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function EntryCard({ entry }: EntryCardProps) {
  return (
    <Link href={`/journals/${entry.id}`}>
      <Card className="hover:shadow-md transition-shadow cursor-pointer group">
        <CardContent className="pt-4">
          {/* Header */}
          <div className="flex items-start justify-between gap-2 mb-2">
            <div className="flex items-center gap-2 flex-wrap">
              <EntryTypeBadge type={entry.type} />
              <span className="text-xs text-muted-foreground flex items-center gap-1">
                <Clock className="h-3 w-3" />
                {formatTime(entry.timestamp)}
              </span>
            </div>
            <div className="flex items-center gap-2">
              {entry.sentiment && (
                <Badge variant="outline" className="text-xs">
                  {entry.sentiment}
                </Badge>
              )}
              <ChevronRight className="h-4 w-4 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity" />
            </div>
          </div>

          {/* Title */}
          <h3 className="font-medium text-sm mb-2">{entry.title}</h3>

          {/* Content */}
          <div className="text-sm text-muted-foreground line-clamp-4">
            <Markdown>{entry.content}</Markdown>
          </div>

          {/* Footer */}
          <div className="flex items-center gap-4 mt-3 pt-3 border-t">
            {/* Tags */}
            {entry.tags.length > 0 && (
              <div className="flex items-center gap-1 flex-wrap">
                <Tag className="h-3 w-3 text-muted-foreground" />
                {entry.tags.slice(0, 3).map((tag) => (
                  <Badge key={tag} variant="outline" className="text-xs">
                    {tag}
                  </Badge>
                ))}
                {entry.tags.length > 3 && (
                  <span className="text-xs text-muted-foreground">
                    +{entry.tags.length - 3}
                  </span>
                )}
              </div>
            )}

            {/* Related Task */}
            {entry.task_id && (
              <Badge variant="outline" className="text-xs">
                <Link2 className="h-3 w-3 mr-1" />
                Task #{entry.task_id.slice(0, 8)}
              </Badge>
            )}
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
