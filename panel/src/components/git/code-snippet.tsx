"use client";

import { useGitFile } from "@/hooks/use-git";
import { Skeleton } from "@/components/ui/skeleton";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";

interface CodeSnippetProps {
  branch: string | null | undefined;
  file: string | null | undefined;
  activeLine?: number | null;
  context?: number;
}

// A compact file-content window for a revision finding: shows the lines around
// `activeLine` from `branch:file`, with the flagged line highlighted. Lazy and
// fail-open — a missing/deleted file renders a muted hint, never breaks the
// finding card. Styled to match git-diff-viewer's <pre> convention.
export function CodeSnippet({
  branch,
  file,
  activeLine,
  context = 10,
}: CodeSnippetProps) {
  const enabled = !!branch && !!file;
  const { data, isLoading, isError } = useGitFile(
    branch,
    file,
    activeLine ?? undefined,
    context,
    enabled,
  );

  if (!enabled) return null;

  if (isLoading) {
    return <Skeleton className="h-24 w-full rounded" />;
  }

  if (isError || !data || !data.content) {
    return (
      <p className="text-xs text-muted-foreground">
        Couldn’t load this file’s content from {branch}.
      </p>
    );
  }

  const lines = data.content.split("\n");
  const active = activeLine ?? null;

  return (
    <div className="rounded border bg-muted/30">
      <ScrollArea className="h-64">
        <pre className="p-2 font-mono text-[11px] leading-relaxed sm:text-xs">
          {lines.map((text, i) => {
            const lineNo = data.start_line + i;
            const isActive = active != null && lineNo === active;
            return (
              <div
                key={i}
                className={cn(
                  "flex gap-3 px-2 -mx-2 rounded-sm",
                  isActive &&
                    "bg-blue-500/15 ring-1 ring-inset ring-blue-500/30",
                )}
              >
                <span className="select-none w-8 shrink-0 text-right text-muted-foreground/60">
                  {lineNo}
                </span>
                <span className="whitespace-pre">{text || " "}</span>
              </div>
            );
          })}
        </pre>
      </ScrollArea>
      {data.truncated && (
        <p className="border-t px-2 py-1 text-[10px] text-muted-foreground">
          showing lines {data.start_line}–{data.start_line + lines.length - 1}{" "}
          of {data.total_lines}
        </p>
      )}
    </div>
  );
}