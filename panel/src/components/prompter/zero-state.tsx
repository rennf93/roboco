"use client";

import { MessageSquarePlus, Zap, ListChecks, GitBranch, Sparkles } from "lucide-react";
import { ModelSelector } from "./model-selector";

interface ZeroStateProps {
  onSelectPrompt: (prompt: string) => void;
}

const EXAMPLE_PROMPTS = [
  {
    icon: Zap,
    label: "Add real-time notifications",
    prompt:
      "Create a task to add real-time push notifications to the dashboard using WebSockets. Users should see alerts when their tasks are updated.",
  },
  {
    icon: ListChecks,
    label: "Write integration tests",
    prompt:
      "Create a task to write integration tests for the task lifecycle API covering all state transitions from pending to completed.",
  },
  {
    icon: GitBranch,
    label: "Refactor API client",
    prompt:
      "Create a task to refactor the frontend API client to use react-query mutations and auto-invalidate caches on write operations.",
  },
  {
    icon: Sparkles,
    label: "Build onboarding wizard",
    prompt:
      "Create a task to build a step-by-step onboarding wizard for new agents, guiding them through their first task assignment.",
  },
];

export function ZeroState({ onSelectPrompt }: ZeroStateProps) {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-8 px-4 py-12 text-center max-w-2xl mx-auto">
      {/* Icon + headline */}
      <div className="flex flex-col items-center gap-4">
        <div className="rounded-full bg-primary/10 p-5">
          <MessageSquarePlus className="h-10 w-10 text-primary" />
        </div>
        <h2 className="text-2xl font-semibold tracking-tight">
          Describe a task, launch it instantly
        </h2>
        <p className="text-muted-foreground max-w-md">
          Tell the AI what you need built. It will draft the task details,
          fill in the fields, and let you review before handing off to your team.
        </p>
      </div>

      {/* Model selector */}
      <div className="flex flex-col items-center gap-2">
        <span className="text-sm text-muted-foreground">Model</span>
        <ModelSelector />
      </div>

      {/* Example prompts */}
      <div className="w-full">
        <p className="text-sm font-medium text-muted-foreground mb-3">
          Try an example
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {EXAMPLE_PROMPTS.map((ex) => (
            <button
              key={ex.label}
              onClick={() => onSelectPrompt(ex.prompt)}
              className="flex items-start gap-3 rounded-lg border bg-card p-4 text-left text-sm hover:bg-muted transition-colors hover:border-primary/50 group"
            >
              <ex.icon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground group-hover:text-primary transition-colors" />
              <span className="font-medium">{ex.label}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
