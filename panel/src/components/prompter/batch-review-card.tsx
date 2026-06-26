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
import type { CellWork, DraftProposal } from "@/lib/api/prompter";
import { Team } from "@/types";

/** The delivery cells a multi-cell draft fans out to (one the_work entry each). */
const CELL_TEAMS: Team[] = [Team.BACKEND, Team.FRONTEND, Team.UX_UI];

const CELL_LABEL: Record<string, string> = {
  backend: "Backend",
  frontend: "Frontend",
  ux_ui: "UX/UI",
};

/** One the_work entry whose team is a delivery cell — a per-cell project picker. */
interface CellEntry {
  entry: CellWork;
  entryIndex: number;
  team: Team;
}

/** A draft's per-cell entries (the the_work slots that carry a cell team), in
 *  the_work order. Empty for a legacy single-cell draft with no cell the_work. */
function cellEntries(draft: DraftProposal): CellEntry[] {
  return (draft.the_work ?? [])
    .map((entry, entryIndex) => ({ entry, entryIndex, team: entry?.team }))
    .filter(
      (e): e is CellEntry =>
        !!e.team && (CELL_TEAMS as readonly string[]).includes(e.team),
    );
}

interface BatchReviewCardProps {
  batch: BatchProposal;
  /** The conflict-free waves (lists of draft indices), once previewed. */
  waves: number[][] | null;
  /** The repos this MegaTask is scoped to — each cell must target one of them. */
  projectIds: string[];
  onKeepChatting: () => void;
  /** `entryIndex` is the the_work slot (the cell); -1 for a legacy single-cell
   *  draft with no per-cell map (sets the top-level project_id). */
  onProjectChange: (
    index: number,
    entryIndex: number,
    projectId: string,
  ) => void;
  onConfirm: (route: StartRoute) => void;
  /** A launch is in flight — disable the actions so a double-click can't dupe. */
  isLaunching?: boolean;
}

/**
 * The MegaTask review card: every task the agent proposed in one batch, each
 * with its per-cell target projects (editable) and collision surface, plus the
 * conflict-free wave plan. A multi-cell task (be+fe, fe+uxui) shows one project
 * picker per cell, scoped to that cell's repos — a RoboCo project is per-cell,
 * so each cell lands in its own repo. The human reviews the whole batch and the
 * sequencing, fixes any cell in the wrong repo, then picks one start path.
 */
export function BatchReviewCard({
  batch,
  waves,
  projectIds,
  onKeepChatting,
  onProjectChange,
  onConfirm,
  isLaunching = false,
}: BatchReviewCardProps) {
  const { data: allProjects = [] } = useProjects();
  // Only the scoped repos are valid targets (the agent read only those).
  const scoped = new Set(projectIds);
  const titleOf = (i: number): string =>
    batch.drafts[i]?.title ?? `Task ${i + 1}`;
  // A task is mis-targeted when any of its cells lacks a scoped project (a
  // multi-cell draft checks every the_work entry; a legacy single-cell draft
  // with no cell map checks its top-level project_id).
  const missingProject = batch.drafts.some((d) => {
    const entries = cellEntries(d);
    if (entries.length > 0) {
      return entries.some(
        (ce) => !ce.entry.project_id || !scoped.has(ce.entry.project_id),
      );
    }
    return !d.project_id || !scoped.has(d.project_id);
  });

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
          {batch.drafts.map((draft, i) => {
            const entries = cellEntries(draft);
            return (
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
                {entries.length > 0 ? (
                  /* Per-cell project picker — one Select per the_work entry,
                     scoped to that cell's repos (a project is per-cell). */
                  <div className="mt-1.5 space-y-1">
                    {entries.map(({ entry, entryIndex, team }) => {
                      const cellProjects = allProjects.filter(
                        (p) => scoped.has(p.id) && p.assigned_cell === team,
                      );
                      const pid = entry.project_id ?? "";
                      const ok = pid !== "" && scoped.has(pid);
                      return (
                        <div
                          key={entryIndex}
                          className="flex items-center gap-2"
                        >
                          <span className="w-16 shrink-0 text-xs text-muted-foreground">
                            {CELL_LABEL[team] ?? team}
                          </span>
                          <Select
                            value={ok ? pid : ""}
                            onValueChange={(v) =>
                              onProjectChange(i, entryIndex, v)
                            }
                            disabled={isLaunching}
                          >
                            <SelectTrigger
                              className={`h-7 flex-1 text-xs ${
                                ok ? "" : "border-destructive"
                              }`}
                            >
                              <SelectValue placeholder="Pick a project…" />
                            </SelectTrigger>
                            <SelectContent>
                              {cellProjects.map((p) => (
                                <SelectItem key={p.id} value={p.id}>
                                  {p.name}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  /* Legacy single-cell draft (no per-cell the_work) — one Select
                     bound to the top-level project_id, scoped to all repos. */
                  <div className="mt-1.5 flex items-center gap-2">
                    <span className="text-xs text-muted-foreground">
                      Project
                    </span>
                    <Select
                      value={
                        draft.project_id && scoped.has(draft.project_id)
                          ? draft.project_id
                          : ""
                      }
                      onValueChange={(v) => onProjectChange(i, -1, v)}
                      disabled={isLaunching}
                    >
                      <SelectTrigger
                        className={`h-7 flex-1 text-xs ${
                          draft.project_id && scoped.has(draft.project_id)
                            ? ""
                            : "border-destructive"
                        }`}
                      >
                        <SelectValue placeholder="Pick a project…" />
                      </SelectTrigger>
                      <SelectContent>
                        {allProjects
                          .filter((p) => scoped.has(p.id))
                          .map((p) => (
                            <SelectItem key={p.id} value={p.id}>
                              {p.name}
                            </SelectItem>
                          ))}
                      </SelectContent>
                    </Select>
                  </div>
                )}
              </li>
            );
          })}
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
            Pick a project for every cell of every task before launching the
            MegaTask.
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
