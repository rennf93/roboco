"use client";

/**
 * TypingIndicator — animated three-dot indicator shown while the LLM is streaming.
 * Deliberately NOT a spinner: uses staggered CSS animations on three dots.
 */
export function TypingIndicator() {
  return (
    <div className="flex items-center gap-1 px-4 py-3 rounded-lg bg-muted w-fit">
      <span className="text-xs text-muted-foreground mr-1">AI is typing</span>
      <span
        className="h-2 w-2 rounded-full bg-muted-foreground animate-bounce"
        style={{ animationDelay: "0ms" }}
      />
      <span
        className="h-2 w-2 rounded-full bg-muted-foreground animate-bounce"
        style={{ animationDelay: "150ms" }}
      />
      <span
        className="h-2 w-2 rounded-full bg-muted-foreground animate-bounce"
        style={{ animationDelay: "300ms" }}
      />
    </div>
  );
}
