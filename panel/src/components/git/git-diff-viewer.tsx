"use client";

import { useState } from "react";
import { GitDiffResponse } from "@/types/git";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";
import { FileCode, FileDiff, WrapText } from "lucide-react";
import { HelpTip } from "@/components/ui/help-tip";

interface GitDiffViewerProps {
  stagedDiff: GitDiffResponse | undefined;
  unstagedDiff: GitDiffResponse | undefined;
  isLoadingStaged: boolean;
  isLoadingUnstaged: boolean;
}

function DiffContent({
  diff,
  isLoading,
  wrap,
}: {
  diff: GitDiffResponse | undefined;
  isLoading: boolean;
  wrap: boolean;
}) {
  if (isLoading) {
    return (
      <div className="p-4 space-y-2">
        {Array.from({ length: 10 }).map((_, i) => (
          <Skeleton key={i} className="h-4 w-full" />
        ))}
      </div>
    );
  }

  if (!diff || !diff.diff) {
    return (
      <div className="p-8 text-center text-muted-foreground">
        <FileDiff className="h-8 w-8 mx-auto mb-2 opacity-50" />
        <p>No changes to display</p>
      </div>
    );
  }

  // Parse diff and colorize
  const lines = diff.diff.split("\n");

  return (
    // overflow-x-auto is the horizontal-scroll affordance for un-wrapped long
    // lines on a phone; smaller mobile font, back to the desktop size at sm+.
    <ScrollArea className="h-96">
      <pre
        className={cn(
          "p-4 font-mono text-[11px] leading-relaxed sm:text-xs",
          wrap ? "whitespace-pre-wrap break-all" : "overflow-x-auto",
        )}
      >
        {lines.map((line, i) => {
          let className = "";
          if (line.startsWith("+") && !line.startsWith("+++")) {
            className = "bg-green-500/10 text-green-700 dark:text-green-400";
          } else if (line.startsWith("-") && !line.startsWith("---")) {
            className = "bg-red-500/10 text-red-700 dark:text-red-400";
          } else if (line.startsWith("@@")) {
            className = "bg-blue-500/10 text-blue-700 dark:text-blue-400";
          } else if (line.startsWith("diff") || line.startsWith("index")) {
            className = "text-muted-foreground font-semibold";
          }

          return (
            <div
              key={i}
              className={cn(
                "px-2 -mx-2",
                wrap ? "whitespace-pre-wrap break-all" : "whitespace-pre",
                className,
              )}
            >
              {line || " "}
            </div>
          );
        })}
      </pre>
    </ScrollArea>
  );
}

export function GitDiffViewer({
  stagedDiff,
  unstagedDiff,
  isLoadingStaged,
  isLoadingUnstaged,
}: GitDiffViewerProps) {
  const stagedCount = stagedDiff?.files_changed || 0;
  const unstagedCount = unstagedDiff?.files_changed || 0;
  const [wrap, setWrap] = useState(false);

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <FileCode className="h-4 w-4" />
            Changes
          </CardTitle>
          <Button
            variant={wrap ? "secondary" : "ghost"}
            size="sm"
            className="h-7 px-2 text-xs"
            aria-pressed={wrap}
            onClick={() => setWrap((w) => !w)}
            title="Toggle line wrap"
          >
            <WrapText className="h-3.5 w-3.5 mr-1" />
            Wrap
          </Button>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        <Tabs defaultValue="unstaged">
          <div className="px-4 border-b">
            <TabsList className="h-9">
              {/* HelpTip wraps an inner span, never the TabsTrigger itself —
                  TooltipTrigger's asChild would clobber the trigger's own
                  data-state and break the active-tab highlight (see
                  task-tabs.tsx for the fuller writeup of this bug class). */}
              <TabsTrigger value="unstaged" className="text-xs gap-1">
                <HelpTip label="Files changed on disk that haven't been staged for the next commit yet">
                  <span className="inline-flex items-center gap-1">
                    Working Directory
                    {unstagedCount > 0 && (
                      <Badge
                        variant="secondary"
                        className="h-4 px-1 text-[10px]"
                      >
                        {unstagedCount}
                      </Badge>
                    )}
                  </span>
                </HelpTip>
              </TabsTrigger>
              <TabsTrigger value="staged" className="text-xs gap-1">
                <HelpTip label="Files staged and ready to be included in the next commit">
                  <span className="inline-flex items-center gap-1">
                    Staged
                    {stagedCount > 0 && (
                      <Badge
                        variant="secondary"
                        className="h-4 px-1 text-[10px]"
                      >
                        {stagedCount}
                      </Badge>
                    )}
                  </span>
                </HelpTip>
              </TabsTrigger>
            </TabsList>
          </div>

          <TabsContent value="unstaged" className="m-0">
            <DiffContent
              diff={unstagedDiff}
              isLoading={isLoadingUnstaged}
              wrap={wrap}
            />
          </TabsContent>

          <TabsContent value="staged" className="m-0">
            <DiffContent
              diff={stagedDiff}
              isLoading={isLoadingStaged}
              wrap={wrap}
            />
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  );
}
