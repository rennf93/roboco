import { describe, it, expect } from "vitest";
import { validateLadder } from "@/components/projects/ladder-validation";
import type { EnvironmentRung } from "@/types";

const rungs = (rows: [string, string][]): EnvironmentRung[] =>
  rows.map(([name, branch]) => ({ name, branch }));

describe("validateLadder", () => {
  it("accepts null (inherits default_branch via the shim)", () => {
    expect(validateLadder(null)).toBeNull();
  });

  it("accepts an empty ladder", () => {
    expect(validateLadder([])).toBeNull();
  });

  it("accepts a clean ordered ladder", () => {
    expect(
      validateLadder(rungs([["head", "dev"], ["prod", "master"]])),
    ).toBeNull();
  });

  it("rejects a rung missing a name", () => {
    expect(validateLadder(rungs([["", "dev"]]))).toMatch(/name and a branch/);
  });

  it("rejects a rung missing a branch", () => {
    expect(validateLadder(rungs([["head", "  "]]))).toMatch(/name and a branch/);
  });

  it("rejects duplicate branches", () => {
    expect(
      validateLadder(rungs([["head", "dev"], ["prod", "dev"]])),
    ).toMatch(/Duplicate branch "dev"/);
  });
});