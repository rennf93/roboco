"use client"

import * as React from "react"
import { Sparkles } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

export interface EmptyStateProps {
  onSelectExample: (prompt: string) => void
  examples?: string[]
  className?: string
}

const DEFAULT_EXAMPLES = [
  "Write a product requirements document for a mobile app",
  "Explain the trade-offs between REST and GraphQL APIs",
  "Generate unit tests for a user authentication service",
]

export function EmptyState({
  onSelectExample,
  examples = DEFAULT_EXAMPLES,
  className,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-6 py-12 px-4 text-center",
        className
      )}
    >
      <div className="flex flex-col items-center gap-2">
        <div className="rounded-full bg-muted p-3">
          <Sparkles className="size-5 text-muted-foreground" />
        </div>
        <h3 className="text-sm font-semibold">Start a conversation</h3>
        <p className="text-sm text-muted-foreground max-w-xs">
          Choose an example prompt below or type your own message.
        </p>
      </div>

      <div className="flex flex-wrap justify-center gap-2">
        {examples.map((example, index) => (
          <Button
            key={index}
            variant="outline"
            size="sm"
            onClick={() => onSelectExample(example)}
            className="text-xs h-auto py-2 px-3 whitespace-normal text-left"
          >
            {example}
          </Button>
        ))}
      </div>
    </div>
  )
}
