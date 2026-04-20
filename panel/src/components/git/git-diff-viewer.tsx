"use client";

import { GitDiffResponse } from "@/types/git";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { FileCode, FileDiff } from "lucide-react";

interface GitDiffViewerProps {
  stagedDiff: GitDiffResponse | undefined;
  unstagedDiff: GitDiffResponse | undefined;
  isLoadingStaged: boolean;
  isLoadingUnstaged: boolean;
}

function DiffContent({ diff, isLoading }: { diff: GitDiffResponse | undefined; isLoading: boolean }) {
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
    <ScrollArea className="h-96">
      <pre className="p-4 text-xs font-mono leading-relaxed">
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
              className={`px-2 -mx-2 whitespace-pre ${className}`}
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

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <FileCode className="h-4 w-4" />
          Changes
        </CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        <Tabs defaultValue="unstaged">
          <div className="px-4 border-b">
            <TabsList className="h-9">
              <TabsTrigger value="unstaged" className="text-xs gap-1">
                Working Directory
                {unstagedCount > 0 && (
                  <Badge variant="secondary" className="h-4 px-1 text-[10px]">
                    {unstagedCount}
                  </Badge>
                )}
              </TabsTrigger>
              <TabsTrigger value="staged" className="text-xs gap-1">
                Staged
                {stagedCount > 0 && (
                  <Badge variant="secondary" className="h-4 px-1 text-[10px]">
                    {stagedCount}
                  </Badge>
                )}
              </TabsTrigger>
            </TabsList>
          </div>

          <TabsContent value="unstaged" className="m-0">
            <DiffContent diff={unstagedDiff} isLoading={isLoadingUnstaged} />
          </TabsContent>

          <TabsContent value="staged" className="m-0">
            <DiffContent diff={stagedDiff} isLoading={isLoadingStaged} />
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  );
}
