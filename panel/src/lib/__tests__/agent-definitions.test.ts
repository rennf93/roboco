import { describe, it, expect } from "vitest";
import {
  getBoardAgents,
  getMainPm,
  getBackendAgents,
  getFrontendAgents,
  getUxAgents,
  getMarketingAgents,
  getSupportAgents,
  type AgentDefinition,
} from "@/lib/agent-definitions";
import { AgentRole, Team } from "@/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const makeAgent = (
  id: string,
  role: AgentRole | null,
  team: Team | null,
): AgentDefinition => ({ id, name: id, role, team });

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const ceo = makeAgent("ceo-1", AgentRole.CEO, Team.BOARD);
const mainPm = makeAgent("main-pm-1", AgentRole.MAIN_PM, Team.MAIN_PM);
const auditor = makeAgent("auditor-1", AgentRole.AUDITOR, Team.BOARD);
const headMarketing = makeAgent("hm-1", AgentRole.HEAD_MARKETING, null);
const productOwner = makeAgent("po-1", AgentRole.PRODUCT_OWNER, Team.BOARD);
const prReviewer = makeAgent("prr-root", AgentRole.PR_REVIEWER, Team.BOARD);
const feReviewer = makeAgent("prr-fe", AgentRole.PR_REVIEWER, Team.FRONTEND);
const beCellPm = makeAgent("be-pm", AgentRole.CELL_PM, Team.BACKEND);
const beDev1 = makeAgent("be-dev-1", AgentRole.DEVELOPER, Team.BACKEND);
const feDev1 = makeAgent("fe-dev-1", AgentRole.DEVELOPER, Team.FRONTEND);
const uxDev = makeAgent("ux-1", AgentRole.QA, Team.UX_UI);
const mktDev = makeAgent("mkt-1", AgentRole.DOCUMENTER, Team.MARKETING);
const prompter = makeAgent("prompter-1", AgentRole.PROMPTER, null);
const secretary = makeAgent("secretary-1", AgentRole.SECRETARY, null);

const ALL_AGENTS: AgentDefinition[] = [
  ceo,
  mainPm,
  auditor,
  headMarketing,
  productOwner,
  prReviewer,
  feReviewer,
  beCellPm,
  beDev1,
  feDev1,
  uxDev,
  mktDev,
  prompter,
  secretary,
];

// ---------------------------------------------------------------------------
// getBoardAgents
// ---------------------------------------------------------------------------

describe("getBoardAgents", () => {
  it("includes agents on BOARD team (excluding CEO and MAIN_PM)", () => {
    const result = getBoardAgents(ALL_AGENTS);
    expect(result).toContainEqual(auditor);
    expect(result).toContainEqual(productOwner);
  });

  it("excludes CEO even though CEO has team=board", () => {
    const result = getBoardAgents(ALL_AGENTS);
    expect(result).not.toContainEqual(ceo);
  });

  it("excludes MAIN_PM (MAIN_PM has its own dedicated section)", () => {
    const result = getBoardAgents(ALL_AGENTS);
    expect(result).not.toContainEqual(mainPm);
  });

  it("includes HEAD_MARKETING role regardless of team", () => {
    const result = getBoardAgents(ALL_AGENTS);
    expect(result).toContainEqual(headMarketing);
  });

  it("excludes the CEO-direct helpers (root PR Reviewer, Intake, Secretary)", () => {
    const result = getBoardAgents(ALL_AGENTS);
    expect(result).not.toContainEqual(prReviewer);
    expect(result).not.toContainEqual(prompter);
    expect(result).not.toContainEqual(secretary);
  });

  it("excludes cell agents (BACKEND, FRONTEND, etc.)", () => {
    const result = getBoardAgents(ALL_AGENTS);
    expect(result).not.toContainEqual(beDev1);
    expect(result).not.toContainEqual(feDev1);
  });

  it("returns an empty array for null input", () => {
    expect(getBoardAgents(null)).toEqual([]);
  });

  it("returns an empty array for undefined input", () => {
    expect(getBoardAgents(undefined)).toEqual([]);
  });

  it("returns an empty array for an empty agent list", () => {
    expect(getBoardAgents([])).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// getMainPm
// ---------------------------------------------------------------------------

describe("getMainPm", () => {
  it("returns agents with MAIN_PM role", () => {
    const result = getMainPm(ALL_AGENTS);
    expect(result).toContainEqual(mainPm);
    expect(result).toHaveLength(1);
  });

  it("excludes agents without MAIN_PM role", () => {
    const result = getMainPm(ALL_AGENTS);
    expect(result).not.toContainEqual(ceo);
    expect(result).not.toContainEqual(beDev1);
  });

  it("returns an empty array for null input", () => {
    expect(getMainPm(null)).toEqual([]);
  });

  it("returns an empty array for undefined input", () => {
    expect(getMainPm(undefined)).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// getBackendAgents
// ---------------------------------------------------------------------------

describe("getBackendAgents", () => {
  it("returns all agents on the BACKEND team", () => {
    const result = getBackendAgents(ALL_AGENTS);
    expect(result).toContainEqual(beDev1);
    expect(result).toContainEqual(beCellPm);
  });

  it("excludes agents from other teams", () => {
    const result = getBackendAgents(ALL_AGENTS);
    expect(result).not.toContainEqual(feDev1);
    expect(result).not.toContainEqual(uxDev);
  });

  it("returns an empty array for null input", () => {
    expect(getBackendAgents(null)).toEqual([]);
  });

  it("returns an empty array for undefined input", () => {
    expect(getBackendAgents(undefined)).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// getFrontendAgents
// ---------------------------------------------------------------------------

describe("getFrontendAgents", () => {
  it("returns all agents on the FRONTEND team, including the cell PR reviewer", () => {
    const result = getFrontendAgents(ALL_AGENTS);
    expect(result).toContainEqual(feDev1);
    expect(result).toContainEqual(feReviewer);
    expect(result).toHaveLength(2);
  });

  it("excludes agents from other teams", () => {
    const result = getFrontendAgents(ALL_AGENTS);
    expect(result).not.toContainEqual(beDev1);
  });

  it("returns an empty array for null input", () => {
    expect(getFrontendAgents(null)).toEqual([]);
  });

  it("returns an empty array for undefined input", () => {
    expect(getFrontendAgents(undefined)).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// getUxAgents
// ---------------------------------------------------------------------------

describe("getUxAgents", () => {
  it("returns all agents on the UX_UI team", () => {
    const result = getUxAgents(ALL_AGENTS);
    expect(result).toContainEqual(uxDev);
    expect(result).toHaveLength(1);
  });

  it("excludes agents from other teams", () => {
    const result = getUxAgents(ALL_AGENTS);
    expect(result).not.toContainEqual(beDev1);
    expect(result).not.toContainEqual(feDev1);
  });

  it("returns an empty array for null input", () => {
    expect(getUxAgents(null)).toEqual([]);
  });

  it("returns an empty array for undefined input", () => {
    expect(getUxAgents(undefined)).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// getMarketingAgents
// ---------------------------------------------------------------------------

describe("getMarketingAgents", () => {
  it("returns all agents on the MARKETING team", () => {
    const result = getMarketingAgents(ALL_AGENTS);
    expect(result).toContainEqual(mktDev);
    expect(result).toHaveLength(1);
  });

  it("excludes agents from other teams", () => {
    const result = getMarketingAgents(ALL_AGENTS);
    expect(result).not.toContainEqual(beDev1);
  });

  it("returns an empty array for null input", () => {
    expect(getMarketingAgents(null)).toEqual([]);
  });

  it("returns an empty array for undefined input", () => {
    expect(getMarketingAgents(undefined)).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// getOnDemandAgents
// ---------------------------------------------------------------------------

describe("getSupportAgents", () => {
  it("returns PROMPTER (Intake) agents", () => {
    const result = getSupportAgents(ALL_AGENTS);
    expect(result).toContainEqual(prompter);
  });

  it("returns SECRETARY agents", () => {
    const result = getSupportAgents(ALL_AGENTS);
    expect(result).toContainEqual(secretary);
  });

  it("returns the root PR Reviewer (team=board)", () => {
    const result = getSupportAgents(ALL_AGENTS);
    expect(result).toContainEqual(prReviewer);
  });

  it("excludes cell PR reviewers — they belong to their cell", () => {
    const result = getSupportAgents(ALL_AGENTS);
    expect(result).not.toContainEqual(feReviewer);
  });

  it("excludes Board members, the CEO, the Main PM, and cell agents", () => {
    const result = getSupportAgents(ALL_AGENTS);
    expect(result).not.toContainEqual(productOwner);
    expect(result).not.toContainEqual(beDev1);
    expect(result).not.toContainEqual(ceo);
    expect(result).not.toContainEqual(mainPm);
  });

  it("returns exactly the three support roles and no others", () => {
    const result = getSupportAgents(ALL_AGENTS);
    expect(result).toHaveLength(3);
    expect(result.map((a) => a.role)).toEqual(
      expect.arrayContaining([
        AgentRole.PROMPTER,
        AgentRole.SECRETARY,
        AgentRole.PR_REVIEWER,
      ]),
    );
  });

  it("returns an empty array for null input", () => {
    expect(getSupportAgents(null)).toEqual([]);
  });

  it("returns an empty array for undefined input", () => {
    expect(getSupportAgents(undefined)).toEqual([]);
  });
});
