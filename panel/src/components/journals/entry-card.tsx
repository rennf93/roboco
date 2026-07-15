"use client";

import { JournalEntry } from "@/types";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Markdown } from "@/components/ui/markdown";
import { CopyButton } from "@/components/ui/copy-button";
import { HelpTip } from "@/components/ui/help-tip";
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
    <Card className="hover:shadow-md transition-shadow group">
      <CardContent className="pt-4">
        {/* Clicking the header/title/content opens the entry. The footer's
            task badge + copy button are deliberately outside this link so
            they aren't nested anchors and don't trigger entry navigation. */}
        <Link
          href={`/journals/${entry.id}`}
          prefetch={false}
          className="block cursor-pointer"
        >
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
        </Link>

        {/* Footer */}
        <div className="flex items-center gap-4 mt-3 pt-3 border-t flex-wrap">
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

          {/* Related Task — quick-link + copy full id */}
          {entry.task_id && (
            <div className="flex items-center gap-1">
              <Link href={`/tasks/${entry.task_id}`} prefetch={false}>
                <HelpTip label={entry.task_id}>
                  <Badge
                    variant="outline"
                    className="text-xs cursor-pointer hover:bg-muted"
                  >
                    <Link2 className="h-3 w-3 mr-1" />
                    Task #{entry.task_id.slice(0, 8)}
                  </Badge>
                </HelpTip>
              </Link>
              <CopyButton value={entry.task_id} className="px-1 py-0.5" />
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
