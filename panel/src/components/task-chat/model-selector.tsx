"use client";

import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Cpu } from "lucide-react";

/**
 * Available AI models for task-creation chat.
 * Extend this list as new providers are added to the platform.
 */
const MODEL_OPTIONS = [
  {
    group: "Anthropic",
    models: [
      { value: "claude-opus-4-5", label: "Claude Opus 4.5" },
      { value: "claude-sonnet-4-5", label: "Claude Sonnet 4.5" },
      { value: "claude-haiku-3-5", label: "Claude Haiku 3.5" },
    ],
  },
  {
    group: "OpenAI",
    models: [
      { value: "gpt-4o", label: "GPT-4o" },
      { value: "gpt-4o-mini", label: "GPT-4o Mini" },
    ],
  },
] as const;

/**
 * Default model for new conversations.
 * Sonnet provides the best cost/quality tradeoff for task-creation workflows.
 */
export const DEFAULT_MODEL = "claude-sonnet-4-5";

interface ModelSelectorProps {
  value: string;
  onValueChange: (value: string) => void;
}

/**
 * RATIONALE — chat-header placement:
 *
 * The model selector lives in the chat panel header, not in a settings
 * page or a sidebar menu, for two reasons:
 *
 * 1. Contextual relevance: The model choice affects the quality of the
 *    structured draft being produced. Users should see and adjust it at the
 *    moment they decide to start a conversation, not buried in settings.
 *
 * 2. Minimal workflow disruption: Placing the control in the header means
 *    the user makes the choice once, before typing — not mid-thought in the
 *    chat input area, and not after the draft has already been generated.
 *    A header placement also follows the established pattern used by most
 *    AI chat UIs (Claude.ai, ChatGPT, Gemini) which trains users to look
 *    there first.
 */
export function ModelSelector({ value, onValueChange }: ModelSelectorProps) {
  return (
    <Select value={value} onValueChange={onValueChange}>
      <SelectTrigger
        size="sm"
        className="w-auto max-w-[200px] gap-1.5"
        aria-label="Select AI model"
      >
        <Cpu className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
        <SelectValue placeholder="Select model" />
      </SelectTrigger>
      <SelectContent align="start">
        {MODEL_OPTIONS.map(({ group, models }) => (
          <SelectGroup key={group}>
            <SelectLabel>{group}</SelectLabel>
            {models.map(({ value: modelValue, label }) => (
              <SelectItem key={modelValue} value={modelValue}>
                {label}
              </SelectItem>
            ))}
          </SelectGroup>
        ))}
      </SelectContent>
    </Select>
  );
}
