"use client"

import * as React from "react"
import { ChevronDown, ChevronUp } from "lucide-react"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { Checkbox } from "@/components/ui/checkbox"
import { cn } from "@/lib/utils"

export interface DraftCardProps {
  title: string
  summary: string
  detail: string
  acceptanceCriteria: string[]
  className?: string
}

export function DraftCard({
  title,
  summary,
  detail,
  acceptanceCriteria,
  className,
}: DraftCardProps) {
  const [isOpen, setIsOpen] = React.useState(false)

  return (
    <Collapsible
      open={isOpen}
      onOpenChange={setIsOpen}
      className={cn(
        "rounded-lg border bg-card text-card-foreground shadow-xs",
        className
      )}
    >
      <div className="flex items-center justify-between p-4">
        <h3 className="text-sm font-semibold">{title}</h3>
        <CollapsibleTrigger asChild>
          <button
            className="rounded-md p-1 hover:bg-accent hover:text-accent-foreground transition-colors"
            aria-label={isOpen ? "Collapse" : "Expand"}
          >
            {isOpen ? (
              <ChevronUp className="size-4" />
            ) : (
              <ChevronDown className="size-4" />
            )}
          </button>
        </CollapsibleTrigger>
      </div>

      {!isOpen && (
        <div className="px-4 pb-4 text-sm text-muted-foreground">{summary}</div>
      )}

      <CollapsibleContent>
        <div className="border-t px-4 py-3 space-y-3">
          <p className="text-sm text-foreground">{detail}</p>

          {acceptanceCriteria.length > 0 && (
            <div className="space-y-2">
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                Acceptance Criteria
              </p>
              <ul className="space-y-1.5">
                {acceptanceCriteria.map((criterion, index) => (
                  <li key={index} className="flex items-start gap-2">
                    <Checkbox
                      id={`criterion-${index}`}
                      className="mt-0.5 shrink-0"
                    />
                    <label
                      htmlFor={`criterion-${index}`}
                      className="text-sm leading-snug cursor-pointer"
                    >
                      {criterion}
                    </label>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}
