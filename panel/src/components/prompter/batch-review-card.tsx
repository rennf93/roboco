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
import { Checkbox } from "@/components/ui/checkbox";
import { useProjects } from "@/hooks/use-projects";
import type { BatchProposal, StartRoute } from "@/hooks/use-prompter";
import type { ProjectSummary } from "@/types";
import type { CellWork, DraftProposal } from "@/lib/api/prompter";
import { Team } from "@/types";
import { HelpTip } from "@/components/ui/help-tip";

/** The delivery cells a multi-cell draft fans out to (one the_work entry each). */
const CELL_TEAMS: Team[] = [Team.BACKEND, Team.FRONTEND, Team.UX_UI];

const CELL_LABEL: Record<string, string> = {
  backend: "Backend",
  frontend: "Frontend",
  ux_ui: "UX/UI",
};

/** The project_ids a draft currently targets: the per-cell ``the_work[].project_id``
 *  set (the multi-select model), falling back to a legacy top-level project_id.
 *  Only scoped ids count — an out-of-scope id is treated as unselected. */
function selectedProjectIds(
  draft: DraftProposal,
  scoped: Set<string>,
): string[] {
  const work = Array.isArray(draft.the_work) ? draft.the_work : [];
  const pids = work
    .filter(
      (w): w is CellWork & { project_id: string } =>
        !!w?.team &&
        (CELL_TEAMS as readonly string[]).includes(w.team) &&
        typeof w.project_id === "string" &&
        w.project_id !== "" &&
        scoped.has(w.project_id),
    )
    .map((w) => w.project_id);
  if (pids.length > 0) return pids;
  if (draft.project_id && scoped.has(draft.project_id))
    return [draft.project_id];
  return [];
}

interface BatchReviewCardProps {
  batch: BatchProposal;
  /** The conflict-free waves (lists of draft indices), once previewed. */
  waves: number[][] | null;
  /** The repos this MegaTask is scoped to — each task targets a subset of them. */
  projectIds: string[];
  onKeepChatting: () => void;
  /** Set the whole set of projects one task targets (multi-select across cells,
   *  one repo per cell — the backend stores one project per cell). */
  onSetProjects: (index: number, ids: string[]) => void;
  onConfirm: (route: StartRoute) => void;
  /** A launch is in flight — disable the actions so a double-click can't dupe. */
  isLaunching?: boolean;
}

/**
 * The MegaTask review card: every task the agent proposed in one batch, each
 * with its target projects (a multi-select checkbox list — one task can span
 * several repos, one repo per delivery cell) and collision surface, plus the
 * conflict-free wave plan. The human reviews the whole batch and the sequencing,
 * picks the repos each task lands in, then picks one start path.
 */
export function BatchReviewCard({
  batch,
  waves,
  projectIds,
  onKeepChatting,
  onSetProjects,
  onConfirm,
  isLaunching = false,
}: BatchReviewCardProps) {
  const { data: allProjects = [] } = useProjects();
  // Only the scoped repos are valid targets (the agent read only those).
  const scoped = new Set(projectIds);
  const scopedByCell = (cell: Team): ProjectSummary[] =>
    allProjects.filter((p) => scoped.has(p.id) && p.assigned_cell === cell);
  const titleOf = (i: number): string =>
    batch.drafts[i]?.title ?? `Task ${i + 1}`;
  // A task is mis-targeted when it has no project selected at all (the backend
  // re-asserts each targeted project is in scope and the batch spans ≥2 repos).
  const missingProject = batch.drafts.some(
    (d) => selectedProjectIds(d, scoped).length === 0,
  );

  /** Toggle one project in a task's selection. A RoboCo project is per-cell and
   *  the backend stores one project per cell, so checking a repo in a cell that
   *  already has a different repo checked swaps it (unchecks the sibling). */
  const toggle = (index: number, projectId: string) => {
    const draft = batch.drafts[index];
    const current = selectedProjectIds(draft, scoped);
    const proj = allProjects.find((p) => p.id === projectId);
    const cell = proj?.assigned_cell;
    if (current.includes(projectId)) {
      onSetProjects(
        index,
        current.filter((id) => id !== projectId),
      );
      return;
    }
    // Checking: drop any other project in the same cell (one repo per cell).
    const next = current.filter((id) => {
      const other = allProjects.find((p) => p.id === id);
      return other?.assigned_cell !== cell;
    });
    onSetProjects(index, [...next, projectId]);
  };

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
            const selected = selectedProjectIds(draft, scoped);
            return (
              <li
                key={i}
                className="rounded-md border bg-background/60 px-3 py-2 text-sm"
              >
                <div className="flex items-start justify-between gap-2">
                  {/* Title is clamped to a fixed 2-line space so a long title
                      can't grow the row (or, as a long unbroken token, blow the
                      card width out and wreck the layout). min-w-0 lets the flex
                      item shrink below min-content; break-words stops a token
                      from overflowing; the full title is on the tooltip. */}
                  <span
                    className="min-w-0 flex-1 break-words font-medium leading-tight line-clamp-2"
                    title={`${i + 1}. ${draft.title}`}
                  >
                    {i + 1}. {draft.title}
                  </span>
                  <div className="flex shrink-0 items-center gap-1">
                    {draft.adds_migration && (
                      <HelpTip label="This task adds a database migration that must be applied in order">
                        <Badge variant="outline" className="gap-1 text-xs">
                          <Database className="h-3 w-3" />
                          migration
                        </Badge>
                      </HelpTip>
                    )}
                    {draft.touches_shared && (
                      <HelpTip label="This task modifies shared code that other tasks may also touch">
                        <Badge variant="outline" className="gap-1 text-xs">
                          <Share2 className="h-3 w-3" />
                          shared
                        </Badge>
                      </HelpTip>
                    )}
                  </div>
                </div>
                {(draft.objective || draft.description) && (
                  <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                    {draft.objective || draft.description}
                  </p>
                )}
                {/* Multi-select project picker — one task can span several repos
                    (one per delivery cell). Grouped by cell; one repo per cell. */}
                <div className="mt-1.5 space-y-1.5">
                  <p
                    className={`text-xs ${
                      selected.length === 0
                        ? "text-destructive"
                        : "text-muted-foreground"
                    }`}
                  >
                    Projects {selected.length === 0 && "— pick at least one"}
                  </p>
                  {CELL_TEAMS.map((cell) => {
                    const repos = scopedByCell(cell);
                    if (repos.length === 0) return null;
                    return (
                      <div key={cell} className="space-y-1">
                        <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                          {CELL_LABEL[cell] ?? cell}
                        </span>
                        <div className="flex flex-wrap gap-x-3 gap-y-1">
                          {repos.map((p) => {
                            const checked = selected.includes(p.id);
                            return (
                              <label
                                key={p.id}
                                className="flex cursor-pointer items-center gap-1.5 text-xs disabled:cursor-not-allowed"
                              >
                                <Checkbox
                                  checked={checked}
                                  disabled={isLaunching}
                                  onCheckedChange={() => toggle(i, p.id)}
                                />
                                <span>{p.name}</span>
                              </label>
                            );
                          })}
                        </div>
                      </div>
                    );
                  })}
                </div>
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
                <li
                  key={w}
                  className="break-words text-xs line-clamp-2"
                  title={wave.map((i) => titleOf(i)).join(", ")}
                >
                  <span className="font-medium">Wave {w + 1}:</span>{" "}
                  {wave.map((i) => titleOf(i)).join(", ")}
                </li>
              ))}
            </ol>
          </div>
        )}

        {missingProject && (
          <p className="text-xs text-destructive">
            Pick at least one project for every task before launching the
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
