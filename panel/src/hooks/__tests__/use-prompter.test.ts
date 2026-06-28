import { describe, expect, it } from "vitest";
import type { CellWork, DraftProposal } from "@/lib/api/prompter";
import { Team } from "@/types";
import {
  fillBatchProjects,
  parkCellWork,
  rebuildCellWork,
  type BatchProposal,
} from "@/hooks/use-prompter";

const BE = "be-repo";
const FE = "fe-repo";
const BE2 = "be-core";

function proj(id: string, cell: Team) {
  return { id, name: id, assigned_cell: cell };
}

function draft(partial: Partial<DraftProposal>): DraftProposal {
  return {
    title: "T",
    description: "objective objective objective",
    acceptance_criteria: ["a"],
    team: Team.BACKEND,
    ...partial,
  } as DraftProposal;
}

function mk(drafts: DraftProposal[]): BatchProposal {
  return { title: "batch", drafts, dropped: 0 };
}

describe("fillBatchProjects", () => {
  it("fills a single-cell draft from the top-level project_id when it matches the cell", () => {
    const batch = mk([
      draft({ the_work: [{ team: Team.BACKEND, summary: "s", items: [] }] }),
    ]);
    const out = fillBatchProjects(batch, [BE], [proj(BE, Team.BACKEND)]);
    expect(out.drafts[0].the_work![0].project_id).toBe(BE);
  });

  it("falls back to the single scoped repo for the cell when no project_id is set", () => {
    const batch = mk([
      draft({ the_work: [{ team: Team.FRONTEND, summary: "s", items: [] }] }),
    ]);
    const out = fillBatchProjects(
      batch,
      [BE, FE],
      [proj(BE, Team.BACKEND), proj(FE, Team.FRONTEND)],
    );
    expect(out.drafts[0].the_work![0].project_id).toBe(FE);
  });

  it("leaves a cell empty when 2+ scoped repos belong to that cell (real ambiguity)", () => {
    const batch = mk([
      draft({ the_work: [{ team: Team.BACKEND, summary: "s", items: [] }] }),
    ]);
    const out = fillBatchProjects(
      batch,
      [BE, BE2],
      [proj(BE, Team.BACKEND), proj(BE2, Team.BACKEND)],
    );
    expect(out.drafts[0].the_work![0].project_id).toBeFalsy();
  });

  it("does not overwrite an explicit per-cell project_id", () => {
    const batch = mk([
      draft({
        the_work: [
          { team: Team.BACKEND, summary: "s", items: [], project_id: BE2 },
        ],
      }),
    ]);
    const out = fillBatchProjects(
      batch,
      [BE, BE2],
      [proj(BE, Team.BACKEND), proj(BE2, Team.BACKEND)],
    );
    expect(out.drafts[0].the_work![0].project_id).toBe(BE2);
  });

  it("does not trust a top-level project_id whose cell mismatches the work entry", () => {
    const batch = mk([
      draft({
        project_id: FE, // frontend repo, but the work says backend
        the_work: [{ team: Team.BACKEND, summary: "s", items: [] }],
      }),
    ]);
    const out = fillBatchProjects(
      batch,
      [FE, BE],
      [proj(FE, Team.FRONTEND), proj(BE, Team.BACKEND)],
    );
    expect(out.drafts[0].the_work![0].project_id).toBe(BE);
  });

  it("is idempotent (returns the same batch reference on a second pass)", () => {
    const batch = mk([
      draft({ the_work: [{ team: Team.BACKEND, summary: "s", items: [] }] }),
    ]);
    const once = fillBatchProjects(batch, [BE], [proj(BE, Team.BACKEND)]);
    const twice = fillBatchProjects(once, [BE], [proj(BE, Team.BACKEND)]);
    expect(twice).toBe(once);
  });

  it("legacy draft (no the_work) keeps a scoped top-level project_id as-is", () => {
    const batch = mk([draft({ the_work: [], project_id: BE })]);
    const out = fillBatchProjects(batch, [BE], [proj(BE, Team.BACKEND)]);
    expect(out).toBe(batch); // nothing to fill → same reference
  });
});

describe("rebuildCellWork", () => {
  // The multi-select review card: the human picks a SET of repos for a task
  // (one repo per delivery cell). rebuildCellWork turns that set into the
  // the_work[] cell map the backend stores (task_cell_projects is unique per
  // (task, team) — one repo per cell).

  const projects = [
    proj(BE, Team.BACKEND),
    proj(BE2, Team.BACKEND),
    proj(FE, Team.FRONTEND),
    proj("ux-repo", Team.UX_UI),
  ];

  it("maps one selected repo per cell and clears the top-level project_id", () => {
    const out = rebuildCellWork([BE, FE], projects, []);
    expect(out.project_id).toBeNull();
    const teams = out.the_work.map((w) => w.team);
    expect(teams).toEqual([Team.BACKEND, Team.FRONTEND]);
    expect(out.the_work[0].project_id).toBe(BE);
    expect(out.the_work[1].project_id).toBe(FE);
  });

  it("keeps only the first selected repo when two are picked for the same cell (one repo per cell)", () => {
    const out = rebuildCellWork([BE, BE2], projects, []);
    expect(out.the_work).toHaveLength(1);
    expect(out.the_work[0].team).toBe(Team.BACKEND);
    expect(out.the_work[0].project_id).toBe(BE); // first wins
  });

  it("preserves an existing cell entry's summary/items when the cell stays selected", () => {
    const current: CellWork[] = [
      {
        team: Team.BACKEND,
        summary: "build API",
        items: ["x", "y"],
        project_id: BE2,
      },
    ];
    const out = rebuildCellWork([BE], projects, current);
    expect(out.the_work).toHaveLength(1);
    expect(out.the_work[0].summary).toBe("build API");
    expect(out.the_work[0].items).toEqual(["x", "y"]);
    expect(out.the_work[0].project_id).toBe(BE); // repo swapped, summary kept
  });

  it("drops a cell entry when its cell is no longer selected", () => {
    const current: CellWork[] = [
      { team: Team.BACKEND, summary: "s", items: [] },
      { team: Team.FRONTEND, summary: "s", items: [] },
    ];
    const out = rebuildCellWork([FE], projects, current);
    expect(out.the_work.map((w) => w.team)).toEqual([Team.FRONTEND]);
  });

  it("appends a minimal entry for a newly-selected cell not in the_work", () => {
    const out = rebuildCellWork([FE, "ux-repo"], projects, [
      { team: Team.BACKEND, summary: "s", items: [] },
    ]);
    const teams = out.the_work.map((w) => w.team);
    expect(teams).toEqual([Team.FRONTEND, Team.UX_UI]); // backend dropped, fe+ux added
    expect(out.the_work[0]).toMatchObject({
      team: Team.FRONTEND,
      summary: "",
      items: [],
    });
  });

  it("empty selection empties the_work (the task then has no project → blocked)", () => {
    const out = rebuildCellWork([], projects, [
      { team: Team.BACKEND, summary: "s", items: [] },
    ]);
    expect(out.the_work).toEqual([]);
    expect(out.project_id).toBeNull();
  });

  // F086: toggling a cell's project off then back on must NOT blank the
  // agent-authored summary/items. The caller parks the cell's last content
  // (the_work snapshot before the toggle-off) and passes it back as
  // priorByCell; rebuildCellWork restores from it instead of appending a
  // blank entry.
  it("restores a re-selected cell's parked summary/items from priorByCell instead of blanking (F086)", () => {
    const prior = new Map<Team, CellWork>([
      [
        Team.BACKEND,
        {
          team: Team.BACKEND,
          summary: "agent-authored backend work",
          items: ["endpoint", "migrations"],
        },
      ],
    ]);
    // currentWork has no backend entry (it was toggled off and dropped) —
    // without priorByCell this would append {summary:"", items:[]}.
    const out = rebuildCellWork([BE], projects, [], prior);
    expect(out.the_work).toHaveLength(1);
    expect(out.the_work[0].team).toBe(Team.BACKEND);
    expect(out.the_work[0].summary).toBe("agent-authored backend work");
    expect(out.the_work[0].items).toEqual(["endpoint", "migrations"]);
    expect(out.the_work[0].project_id).toBe(BE); // restored content, new repo
  });

  it("a freshly-selected cell with no parked copy still gets a blank entry", () => {
    // priorByCell has no frontend entry — append blank, unchanged behavior.
    const prior = new Map<Team, CellWork>([
      [Team.BACKEND, { team: Team.BACKEND, summary: "s", items: ["i"] }],
    ]);
    const out = rebuildCellWork([FE], projects, [], prior);
    expect(out.the_work[0]).toMatchObject({
      team: Team.FRONTEND,
      summary: "",
      items: [],
    });
  });

  it("an existing selected entry wins over a stale parked copy (edits are kept)", () => {
    // The cell is currently selected with edited content; priorByCell holds
    // an older copy. The live entry must win — restore must not regress an
    // in-place edit back to the stale parked copy.
    const current: CellWork[] = [
      {
        team: Team.BACKEND,
        summary: "edited just now",
        items: ["new"],
        project_id: BE2,
      },
    ];
    const prior = new Map<Team, CellWork>([
      [Team.BACKEND, { team: Team.BACKEND, summary: "stale", items: ["old"] }],
    ]);
    const out = rebuildCellWork([BE], projects, current, prior);
    expect(out.the_work[0].summary).toBe("edited just now");
    expect(out.the_work[0].items).toEqual(["new"]);
    expect(out.the_work[0].project_id).toBe(BE);
  });
});

describe("parkCellWork — parked snapshot across a toggle-off/on (F086)", () => {
  it("retains a deselected cell's content so a later re-select can restore it", () => {
    // work still has backend (about to be toggled off); no prior parking yet.
    const parked = parkCellWork(
      [],
      [{ team: Team.BACKEND, summary: "agent work", items: ["a"] }],
    );
    expect(parked).toEqual([
      { team: Team.BACKEND, summary: "agent work", items: ["a"] },
    ]);

    // Now the cell is toggled off — work no longer has it, but the parked
    // snapshot retains it.
    const parked2 = parkCellWork(parked, []);
    expect(parked2.map((w) => w.team)).toContain(Team.BACKEND);
    expect(parked2.find((w) => w.team === Team.BACKEND)?.summary).toBe(
      "agent work",
    );
  });

  it("a live entry overwrites a stale parked copy (edits are parked)", () => {
    const parked = parkCellWork(
      [{ team: Team.BACKEND, summary: "old", items: [] }],
      [{ team: Team.BACKEND, summary: "edited", items: ["x"] }],
    );
    expect(parked).toHaveLength(1);
    expect(parked[0].summary).toBe("edited");
    expect(parked[0].items).toEqual(["x"]);
  });

  it("merges parked cells for different teams with the live ones", () => {
    const parked = parkCellWork(
      [{ team: Team.BACKEND, summary: "parked-be", items: [] }],
      [{ team: Team.FRONTEND, summary: "live-fe", items: ["y"] }],
    );
    const byTeam = new Map(parked.map((w) => [w.team, w]));
    expect(byTeam.get(Team.BACKEND)?.summary).toBe("parked-be");
    expect(byTeam.get(Team.FRONTEND)?.summary).toBe("live-fe");
  });
});
