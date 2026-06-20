import { describe, it, expect } from "vitest";
import {
  getBoardAgents,
  getMainPm,
  getBackendAgents,
  getFrontendAgents,
  getUxAgents,
  getMarketingAgents,
  getOnDemandAgents,
  type AgentDefinition,
} from "@/lib/agent-definitions";
import { AgentRole, Team } from "@/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const makeAgent = (
  id: string,
  role: AgentRole | null,
  team: Team | null
): AgentDefinition => ({ id, name: id, role, team });

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const ceo = makeAgent("ceo-1", AgentRole.CEO, Team.BOARD);
const mainPm = makeAgent("main-pm-1", AgentRole.MAIN_PM, Team.MAIN_PM);
const auditor = makeAgent("auditor-1", AgentRole.AUDITOR, Team.BOARD);
const headMarketing = makeAgent("hm-1", AgentRole.HEAD_MARKETING, null);
const productOwner = makeAgent("po-1", AgentRole.PRODUCT_OWNER, Team.BOARD);
const prReviewer = makeAgent("prr-1", AgentRole.PR_REVIEWER, null);
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

  it("includes PR_REVIEWER role regardless of team", () => {
    const result = getBoardAgents(ALL_AGENTS);
    expect(result).toContainEqual(prReviewer);
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
  it("returns all agents on the FRONTEND team", () => {
    const result = getFrontendAgents(ALL_AGENTS);
    expect(result).toContainEqual(feDev1);
    expect(result).toHaveLength(1);
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

describe("getOnDemandAgents", () => {
  it("returns PROMPTER agents", () => {
    const result = getOnDemandAgents(ALL_AGENTS);
    expect(result).toContainEqual(prompter);
  });

  it("returns SECRETARY agents", () => {
    const result = getOnDemandAgents(ALL_AGENTS);
    expect(result).toContainEqual(secretary);
  });

  it("excludes agents that are neither PROMPTER nor SECRETARY", () => {
    const result = getOnDemandAgents(ALL_AGENTS);
    expect(result).not.toContainEqual(beDev1);
    expect(result).not.toContainEqual(ceo);
    expect(result).not.toContainEqual(mainPm);
  });

  it("returns both on-demand roles and no others", () => {
    const result = getOnDemandAgents(ALL_AGENTS);
    expect(result).toHaveLength(2);
    expect(result.map((a) => a.role)).toEqual(
      expect.arrayContaining([AgentRole.PROMPTER, AgentRole.SECRETARY])
    );
  });

  it("returns an empty array for null input", () => {
    expect(getOnDemandAgents(null)).toEqual([]);
  });

  it("returns an empty array for undefined input", () => {
    expect(getOnDemandAgents(undefined)).toEqual([]);
  });
});
