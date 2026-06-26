import type { Team, TaskType, TaskNature, Complexity } from "@/types";

// ---------------------------------------------------------------------------
// Prompter draft types — shared by the live intake hook (`prompter-live.ts`),
// the draft card, and the confirm dialog. The chat itself is driven by the
// spawned agent over SSE (`prompter-live.ts`); these are just the shapes of
// the structured draft the agent proposes and the human confirms.
// ---------------------------------------------------------------------------

/** One cell's slice of the work — the per-cell breakdown of The Work.
 *  `project_id` is the per-cell repo for a multi-cell MegaTask draft (a RoboCo
 *  project is per-cell; a monorepo is N per-cell projects sharing one git_url).
 *  A single-cell draft keeps its project at the DraftProposal top level. */
export interface CellWork {
  team: Team;
  summary: string;
  items: string[];
  project_id?: string | null;
}

/** A structured task draft, mirroring the backend PrompterDraftTask. */
export interface DraftProposal {
  title: string;
  description: string;
  acceptance_criteria: string[];
  team: Team;
  priority?: number;
  task_type?: TaskType;
  nature?: TaskNature;
  estimated_complexity?: Complexity;
  // Structured spec fields
  objective?: string | null;
  what_this_builds?: string[];
  the_work?: CellWork[];
  notes?: string[];
  // Sequenced batch intake collision surface (lower = first; analyzer-derived)
  intends_to_touch?: string[];
  adds_migration?: boolean;
  touches_shared?: boolean;
  // Targeting (resolved at confirm time)
  project_id?: string | null;
  product_id?: string | null;
}

/** Single-cell project, board-led multi-cell product, or a multi-project MegaTask. */
export type DraftScale = "single" | "multi" | "megatask";

/** What the human picked/edited at confirm time. `route` is which start button:
 *  "board" (Board review & Start) or "main_pm" (Approve & Start). */
export interface ConfirmPayload {
  project_id?: string;
  product_id?: string;
  draft?: DraftProposal;
  route?: "board" | "main_pm";
  // Set on a board-informed re-draft: the confirm updates this existing task in
  // place instead of creating a new one (scope is taken from the task).
  task_id?: string;
}

/** Confirm a MegaTask: the umbrella's title + one draft per task (each carrying
 *  its own `project_id` + collision surface) + the scoped repos it spans + which
 *  start button. */
export interface BatchConfirmPayload {
  title: string;
  drafts: DraftProposal[];
  project_ids: string[];
  route?: "board" | "main_pm";
}

/** The backend's MegaTask create result: the umbrella, its root-subtasks, and
 *  the computed conflict-free waves (each wave is a list of draft indices). */
export interface BatchConfirmResult {
  umbrella_task_id: string;
  root_subtask_ids: string[];
  waves: number[][];
  warnings: string[];
}

/** The wave plan for a MegaTask, computed without creating anything — shown so
 *  the human can review the sequencing before confirming. */
export interface BatchPreviewResult {
  waves: number[][];
  warnings: string[];
}
