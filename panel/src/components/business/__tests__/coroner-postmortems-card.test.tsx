import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import type { Postmortem } from "@/lib/api/coroner";

const { listPostmortems } = vi.hoisted(() => ({
  listPostmortems: vi.fn(async () => [] as Postmortem[]),
}));

vi.mock("@/lib/api", () => ({
  coronerApi: { listPostmortems },
}));

import { CoronerPostmortemsCard } from "../coroner-postmortems-card";

function withQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

const SAMPLE_POSTMORTEM: Postmortem = {
  task_id: "pm-1",
  title: "Coroner postmortem",
  completed_at: "2026-07-24T10:00:00Z",
  incident_task_id: "abc12345-0000-0000-0000-000000000000",
  incident_kind: "bounced",
  incident_title: "Fix worktree venv rot",
  incident_summary: "The task bounced 3 times over a stale venv symptom.",
  root_cause: "The gate never verified the venv's dev extras were installed.",
  failed_stage: "awaiting_qa",
  process_change_kind: "conventions_rule",
  process_change_description: "Add a venv-freshness check to make quality.",
  playbook_id: null,
};

describe("CoronerPostmortemsCard", () => {
  beforeEach(() => {
    listPostmortems.mockClear();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows the empty state when there are no postmortems yet", async () => {
    render(withQueryClient(<CoronerPostmortemsCard />));
    await waitFor(() =>
      expect(screen.getByText(/no postmortems yet/i)).toBeInTheDocument(),
    );
  });

  it("renders a completed postmortem's incident, root cause, and process change", async () => {
    listPostmortems.mockResolvedValueOnce([SAMPLE_POSTMORTEM]);
    render(withQueryClient(<CoronerPostmortemsCard />));

    await waitFor(() =>
      expect(screen.getByText("Fix worktree venv rot")).toBeInTheDocument(),
    );
    expect(screen.getByText("bounced 3+ times")).toBeInTheDocument();
    expect(screen.getByText("awaiting_qa")).toBeInTheDocument();
    expect(screen.getByText("conventions_rule")).toBeInTheDocument();
    expect(
      screen.getByText(/stale venv symptom/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/never verified the venv's dev extras/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/add a venv-freshness check/i),
    ).toBeInTheDocument();
  });

  it("shows an offline state and retries on error", async () => {
    listPostmortems.mockRejectedValueOnce(new Error("network down"));
    render(withQueryClient(<CoronerPostmortemsCard />));

    await waitFor(() =>
      expect(
        screen.getByText(/failed to load postmortems/i),
      ).toBeInTheDocument(),
    );
  });
});
