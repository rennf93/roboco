import type { EnvironmentRung } from "@/types";

// Validate a ladder before submit. Returns an error string or null when valid.
// null/empty is valid (inherits default_branch via the backend shim).
export function validateLadder(rungs: EnvironmentRung[] | null): string | null {
  if (!rungs || rungs.length === 0) return null;
  const branches: string[] = [];
  for (const rung of rungs) {
    if (!rung.name.trim() || !rung.branch.trim()) {
      return "Every environment rung needs both a name and a branch.";
    }
    const branch = rung.branch.trim();
    if (branches.includes(branch)) {
      return `Duplicate branch "${branch}" — each rung must target a unique branch.`;
    }
    branches.push(branch);
  }
  return null;
}