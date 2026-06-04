"use client"

import * as React from "react"
import { RotateCcw } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

export interface HistoryItem {
  id: string
  title: string
}

export interface HistoryViewProps {
  items?: HistoryItem[]
  onReuse: (id: string) => void
  className?: string
}

const DEFAULT_ITEMS: HistoryItem[] = [
  { id: "1", title: "Summarize the latest sprint retrospective" },
  { id: "2", title: "Write a user story for the login feature" },
]

export function HistoryView({
  items = DEFAULT_ITEMS,
  onReuse,
  className,
}: HistoryViewProps) {
  return (
    <div
      className={cn(
        "flex flex-col gap-1 overflow-y-auto max-h-80",
        className
      )}
    >
      {items.length === 0 ? (
        <p className="text-sm text-muted-foreground text-center py-6">
          No saved prompts yet.
        </p>
      ) : (
        items.map((item) => (
          <div
            key={item.id}
            className="flex items-center justify-between gap-3 rounded-md px-3 py-2.5 hover:bg-accent transition-colors group"
          >
            <span className="text-sm truncate flex-1">{item.title}</span>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={() => onReuse(item.id)}
              aria-label={`Reuse: ${item.title}`}
              className="shrink-0 opacity-0 group-hover:opacity-100 transition-opacity"
            >
              <RotateCcw className="size-3.5" />
            </Button>
          </div>
        ))
      )}
    </div>
  )
}
