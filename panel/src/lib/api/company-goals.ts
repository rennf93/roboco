import api from "./client";

export interface CompanyGoals {
  north_star: string;
  objectives: Record<string, unknown>[];
  constraints: string[];
  operating_policy: Record<string, unknown>;
  updated_at?: string | null;
  updated_by?: string | null;
}

export type CompanyGoalsUpdate = Partial<
  Pick<
    CompanyGoals,
    "north_star" | "objectives" | "constraints" | "operating_policy"
  >
>;

export const companyGoalsApi = {
  // GET /api/company-goals — the charter (any authenticated agent).
  get: async (): Promise<CompanyGoals> => {
    const { data } = await api.get<CompanyGoals>("/company-goals");
    return data;
  },
  // PUT /api/company-goals — CEO-only; partial update, returns the full charter.
  update: async (update: CompanyGoalsUpdate): Promise<CompanyGoals> => {
    const { data } = await api.put<CompanyGoals>("/company-goals", update);
    return data;
  },
};
