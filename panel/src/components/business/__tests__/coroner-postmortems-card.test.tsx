import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import type { Postmortem } from "@/lib/api/coroner";

const { listPostmortems, approveProcessChange, rejectProcessChange } =
  vi.hoisted(() => ({
    listPostmortems: vi.fn(async () => [] as Postmortem[]),
    approveProcessChange: vi.fn(async () => ({
      status: "approved",
      materialized_task_id: "task-1",
      detail: "materialized as a Main-PM-owned task",
    })),
    rejectProcessChange: vi.fn(async () => ({
      status: "rejected",
      detail: "dismissed; feeds the next cycle's prompt",
    })),
  }));

vi.mock("@/lib/api", () => ({
  coronerApi: { listPostmortems, approveProcessChange, rejectProcessChange },
}));

import { CoronerPostmortemsCard } from "../coroner-postmortems-card";

function withQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

function buildPostmortem(overrides: Partial<Postmortem> = {}): Postmortem {
  return {
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
    process_change_status: "proposed",
    process_change_reject_reason: null,
    process_change_materialized_task_id: null,
    ...overrides,
  };
}

const SAMPLE_POSTMORTEM: Postmortem = buildPostmortem();

describe("CoronerPostmortemsCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
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
    expect(screen.getByText(/stale venv symptom/i)).toBeInTheDocument();
    expect(
      screen.getByText(/never verified the venv's dev extras/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/add a venv-freshness check/i)).toBeInTheDocument();
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

  // Defect 2 fix: a proposed process change now gets its own approve/dismiss
  // action.
  it("renders approve/dismiss controls for a proposed process change", async () => {
    listPostmortems.mockResolvedValueOnce([SAMPLE_POSTMORTEM]);
    render(withQueryClient(<CoronerPostmortemsCard />));

    await screen.findByText("Fix worktree venv rot");
    expect(
      screen.getByRole("button", { name: /approve/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /dismiss/i }),
    ).toBeInTheDocument();
  });

  it("hides the actions once the process change already drafted a playbook", async () => {
    listPostmortems.mockResolvedValueOnce([
      buildPostmortem({
        process_change_kind: "playbook",
        process_change_status: "not_applicable",
      }),
    ]);
    render(withQueryClient(<CoronerPostmortemsCard />));

    await screen.findByText("Fix worktree venv rot");
    expect(
      screen.queryByRole("button", { name: /approve/i }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("Drafted as playbook")).toBeInTheDocument();
  });

  it("approves the process change via the API", async () => {
    const user = userEvent.setup();
    listPostmortems.mockResolvedValueOnce([SAMPLE_POSTMORTEM]);
    render(withQueryClient(<CoronerPostmortemsCard />));

    await screen.findByText("Fix worktree venv rot");
    await user.click(screen.getByRole("button", { name: /approve/i }));

    await waitFor(() =>
      expect(approveProcessChange).toHaveBeenCalledWith("pm-1"),
    );
  });

  it("dismisses the process change with a reason via the dialog", async () => {
    const user = userEvent.setup();
    listPostmortems.mockResolvedValueOnce([SAMPLE_POSTMORTEM]);
    render(withQueryClient(<CoronerPostmortemsCard />));

    await screen.findByText("Fix worktree venv rot");
    await user.click(screen.getByRole("button", { name: /dismiss/i }));

    const dialog = await screen.findByRole("dialog");
    await user.type(screen.getByLabelText("Reason"), "one-off incident");
    await user.click(
      within(dialog).getByRole("button", { name: /^dismiss$/i }),
    );

    await waitFor(() =>
      expect(rejectProcessChange).toHaveBeenCalledWith(
        "pm-1",
        "one-off incident",
      ),
    );
  });
});
