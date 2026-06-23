"use client";

import {
  MessageCircle,
  Users,
  Rocket,
  Loader2,
  Database,
  Share2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useProjects } from "@/hooks/use-projects";
import type { BatchProposal, StartRoute } from "@/hooks/use-prompter";

interface BatchReviewCardProps {
  batch: BatchProposal;
  /** The conflict-free waves (lists of draft indices), once previewed. */
  waves: number[][] | null;
  onKeepChatting: () => void;
  onProjectChange: (index: number, projectId: string) => void;
  onConfirm: (route: StartRoute) => void;
  /** A launch is in flight — disable the actions so a double-click can't dupe. */
  isLaunching?: boolean;
}

/**
 * The MegaTask review card: every task the agent proposed in one batch, each
 * with its target project (editable) and collision surface, plus the
 * conflict-free wave plan. The human reviews the whole batch and the sequencing,
 * fixes any task in the wrong repo, then picks one start path for all of them.
 */
export function BatchReviewCard({
  batch,
  waves,
  onKeepChatting,
  onProjectChange,
  onConfirm,
  isLaunching = false,
}: BatchReviewCardProps) {
  const { data: projects = [] } = useProjects();
  const titleOf = (i: number): string =>
    batch.drafts[i]?.title ?? `Task ${i + 1}`;
  const missingProject = batch.drafts.some((d) => !d.project_id);

  return (
    <Card className="border-primary/40 bg-primary/5">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-sm font-semibold leading-tight">
            MegaTask: {batch.title || "Untitled"}
          </CardTitle>
          <Badge variant="secondary" className="shrink-0 text-xs">
            {batch.drafts.length} tasks
          </Badge>
        </div>
        <p className="text-xs text-muted-foreground">
          One batch, sequenced into conflict-free waves. Each task keeps its own
          project, branch, and PR; the Main PM coordinates them all.
        </p>
      </CardHeader>

      <CardContent className="space-y-3 pb-3">
        <ol className="space-y-2">
          {batch.drafts.map((draft, i) => (
            <li
              key={i}
              className="rounded-md border bg-background/60 px-3 py-2 text-sm"
            >
              <div className="flex items-start justify-between gap-2">
                <span className="font-medium leading-tight">
                  {i + 1}. {draft.title}
                </span>
                <div className="flex shrink-0 items-center gap-1">
                  {draft.adds_migration && (
                    <Badge variant="outline" className="gap-1 text-xs">
                      <Database className="h-3 w-3" />
                      migration
                    </Badge>
                  )}
                  {draft.touches_shared && (
                    <Badge variant="outline" className="gap-1 text-xs">
                      <Share2 className="h-3 w-3" />
                      shared
                    </Badge>
                  )}
                </div>
              </div>
              {(draft.objective || draft.description) && (
                <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                  {draft.objective || draft.description}
                </p>
              )}
              {/* Per-task project — editable so a misfiled task can be fixed */}
              <div className="mt-1.5 flex items-center gap-2">
                <span className="text-xs text-muted-foreground">Project</span>
                <Select
                  value={draft.project_id ?? ""}
                  onValueChange={(v) => onProjectChange(i, v)}
                  disabled={isLaunching}
                >
                  <SelectTrigger
                    className={`h-7 flex-1 text-xs ${
                      draft.project_id ? "" : "border-destructive"
                    }`}
                  >
                    <SelectValue placeholder="Pick a project…" />
                  </SelectTrigger>
                  <SelectContent>
                    {projects.map((p) => (
                      <SelectItem key={p.id} value={p.id}>
                        {p.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </li>
          ))}
        </ol>

        {/* Wave plan — how the batch will be sequenced */}
        {waves && waves.length > 0 && (
          <div className="rounded-md border border-dashed px-3 py-2">
            <p className="mb-1 text-xs font-medium text-muted-foreground">
              Wave plan ({waves.length} wave{waves.length === 1 ? "" : "s"})
            </p>
            <ol className="space-y-0.5">
              {waves.map((wave, w) => (
                <li key={w} className="text-xs">
                  <span className="font-medium">Wave {w + 1}:</span>{" "}
                  {wave.map((i) => titleOf(i)).join(", ")}
                </li>
              ))}
            </ol>
          </div>
        )}

        {missingProject && (
          <p className="text-xs text-destructive">
            Pick a project for every task before launching the MegaTask.
          </p>
        )}

        <div className="flex flex-wrap gap-2 pt-1">
          <Button
            variant="outline"
            size="sm"
            onClick={onKeepChatting}
            disabled={isLaunching}
          >
            <MessageCircle className="mr-1.5 h-3.5 w-3.5" />
            Keep chatting
          </Button>
          {/* Board review & Start → the Board reviews the whole batch first */}
          <Button
            variant="secondary"
            size="sm"
            onClick={() => onConfirm("board")}
            disabled={isLaunching || missingProject}
          >
            {isLaunching ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Users className="mr-1.5 h-3.5 w-3.5" />
            )}
            Board review &amp; Start
          </Button>
          {/* Approve & Start → straight to the Main PM, waves dispatch at once */}
          <Button
            size="sm"
            onClick={() => onConfirm("main_pm")}
            disabled={isLaunching || missingProject}
          >
            {isLaunching ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Rocket className="mr-1.5 h-3.5 w-3.5" />
            )}
            Approve &amp; Start
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
