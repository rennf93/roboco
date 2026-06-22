import api from "./client";

// Mirrors roboco.foundation.policy.conventions.models — kept in sync by the
// TS<->Python parity test (tests/unit/foundation/policy/conventions/test_ts_parity.py).
export type RuleLevel = "warn" | "block";
export type DefinitionKind =
  | "model"
  | "route"
  | "helper"
  | "business_logic"
  | "component"
  | "other";

export interface ConventionsModule {
  path: string;
  purpose: string;
  forbidden: DefinitionKind[];
}

export interface ConventionsRule {
  name: string;
  level: RuleLevel;
}

export interface ConventionsCustomRule {
  id: string;
  pattern: string;
  message: string;
  level: RuleLevel;
  languages: string[];
}

export interface ConventionsWaiver {
  path: string;
  rule: string;
  reason: string;
}

export interface ConventionsStandard {
  version: number;
  languages: string[];
  modules: ConventionsModule[];
  rules: Record<string, ConventionsRule>;
  custom: ConventionsCustomRule[];
  waivers: ConventionsWaiver[];
}

export interface ConventionsHealth {
  status: string;
  head_sha: string;
  last_ok_sha: string | null;
}

export interface ConventionsResponse {
  standard: ConventionsStandard;
  health: ConventionsHealth;
}

export interface ConventionsActionResult {
  pr_number: number | null;
  branch: string;
  created: boolean;
}

export const conventionsApi = {
  // GET the project's effective map + health.
  get: async (projectId: string): Promise<ConventionsResponse> => {
    const { data } = await api.get<ConventionsResponse>(
      `/projects/${projectId}/conventions`,
    );
    return data;
  },
  // PUT an edited standard — opens a PR committing it back (PM+).
  update: async (
    projectId: string,
    standard: ConventionsStandard,
  ): Promise<ConventionsActionResult> => {
    const { data } = await api.put<ConventionsActionResult>(
      `/projects/${projectId}/conventions`,
      standard,
    );
    return data;
  },
  // POST restore — re-commits the file from the last-good map (PM+).
  restore: async (projectId: string): Promise<ConventionsActionResult> => {
    const { data } = await api.post<ConventionsActionResult>(
      `/projects/${projectId}/conventions/restore`,
      {},
    );
    return data;
  },
};
