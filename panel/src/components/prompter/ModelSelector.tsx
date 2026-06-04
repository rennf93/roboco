"use client"

import * as React from "react"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { cn } from "@/lib/utils"

export interface ModelSelectorProps {
  models: string[]
  selected: string
  onSelect: (model: string) => void
  className?: string
}

export function ModelSelector({
  models,
  selected,
  onSelect,
  className,
}: ModelSelectorProps) {
  return (
    <Select value={selected} onValueChange={onSelect}>
      <SelectTrigger className={cn("w-[200px]", className)}>
        <SelectValue placeholder="Select a model" />
      </SelectTrigger>
      <SelectContent>
        {models.map((model) => (
          <SelectItem key={model} value={model}>
            {model}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
