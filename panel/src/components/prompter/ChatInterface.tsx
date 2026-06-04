"use client"

import * as React from "react"
import { cn } from "@/lib/utils"

export interface ChatMessage {
  id: string
  role: "user" | "assistant"
  content: string
}

export interface ChatInterfaceProps {
  messages: ChatMessage[]
  className?: string
}

export function ChatInterface({ messages, className }: ChatInterfaceProps) {
  return (
    <div
      className={cn(
        "flex flex-col gap-3 overflow-y-auto p-4",
        className
      )}
    >
      {messages.map((message) => (
        <div
          key={message.id}
          className={cn(
            "flex",
            message.role === "user" ? "justify-end" : "justify-start"
          )}
        >
          <div
            className={cn(
              "max-w-[75%] rounded-2xl px-4 py-2.5 text-sm",
              message.role === "user"
                ? "bg-primary text-primary-foreground rounded-br-sm"
                : "bg-muted text-muted-foreground rounded-bl-sm"
            )}
          >
            {message.content}
          </div>
        </div>
      ))}
    </div>
  )
}
