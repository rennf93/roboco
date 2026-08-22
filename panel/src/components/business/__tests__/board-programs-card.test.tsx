import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import type { BoardProgram, BoardProgramCycle } from "@/lib/api/board-programs";

const { resolveListRef } = vi.hoisted(() => ({
  resolveListRef: { current: null as null | ((v: unknown) => void) },
}));

function buildProgram(overrides: Partial<BoardProgram> = {}): BoardProgram {
  return {
    key: "roadmap",
    title: "Roadmap Cycle",
    description: "Weekly PO exploration proposing roadmap items.",
    role: "product_owner",
    trigger: "cron",
    scope: "org",
    enabled: true,
    opted_in_project_slugs: [],
    last_opened_at: null,
    open_cycle: false,
    last_cycle_summary: null,
    ...overrides,
  };
}

function buildCycle(overrides: Partial<BoardProgramCycle> = {}): BoardProgramCycle {
  return {
    id: "cycle-1",
    opened_at: "2026-08-01T00:00:00Z",
    closed_at: "2026-08-01T01:00:00Z",
    items_proposed: 1,
    items_approved: 1,
    items_rejected: 0,
    nothing_to_propose_reason: null,
    decisions: [
      {
        item_ref: "README claims a dead feature",
        verdict: "approved",
        reason: null,
        item_snapshot: { title: "README claims a dead feature" },
      },
    ],
    ...overrides,
  };
}

const { list, runNow, cycles } = vi.hoisted(() => ({
  list: vi.fn(
    () =>
      new Promise((r) => {
        resolveListRef.current = r as (v: unknown) => void;
      }),
  ),
  runNow: vi.fn(async () => buildProgram({ open_cycle: true })),
  cycles: vi.fn(async () => [] as unknown[]),
}));

vi.mock("@/lib/api/board-programs", () => ({
  boardProgramsApi: { list, runNow, cycles },
}));

const { setFeatureFlag } = vi.hoisted(() => ({
  setFeatureFlag: vi.fn(async () => undefined),
}));
vi.mock("@/lib/api/settings", () => ({
  settingsApi: { setFeatureFlag },
}));

const { toast } = vi.hoisted(() => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));
vi.mock("sonner", () => ({ toast }));

import { BoardProgramsCard } from "../board-programs-card";

function withQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

describe("BoardProgramsCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resolveListRef.current = null;
  });

  it("shows a loading state before the list resolves", () => {
    render(withQueryClient(<BoardProgramsCard />));
    expect(
      document.querySelectorAll('[data-slot="skeleton"]').length,
    ).toBeGreaterThan(0);
    expect(screen.queryByText("roadmap")).not.toBeInTheDocument();
  });

  it("shows an empty state when no programs are registered", async () => {
    render(withQueryClient(<BoardProgramsCard />));
    resolveListRef.current?.([]);
    expect(
      await screen.findByText("No Board Programs registered yet."),
    ).toBeInTheDocument();
  });

  it("renders each program's key, role, trigger, and scope", async () => {
    render(withQueryClient(<BoardProgramsCard />));
    resolveListRef.current?.([
      buildProgram({ key: "roadmap", role: "product_owner" }),
      buildProgram({
        key: "x_feature",
        role: "head_marketing",
        trigger: "cron",
      }),
    ]);

    expect(await screen.findByText("roadmap")).toBeInTheDocument();
    expect(screen.getByText("x_feature")).toBeInTheDocument();
    expect(screen.getByText("product_owner")).toBeInTheDocument();
    expect(screen.getByText("head_marketing")).toBeInTheDocument();
  });

  it("shows a cycle-open badge and disables Run now while a cycle is open", async () => {
    render(withQueryClient(<BoardProgramsCard />));
    resolveListRef.current?.([buildProgram({ open_cycle: true })]);

    expect(await screen.findByText("cycle open")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Run now/i })).toBeDisabled();
  });

  it("calls run-now and shows a success toast", async () => {
    render(withQueryClient(<BoardProgramsCard />));
    resolveListRef.current?.([buildProgram({ open_cycle: false })]);

    const runButton = await screen.findByRole("button", { name: /Run now/i });
    expect(runButton).not.toBeDisabled();
    fireEvent.click(runButton);

    await waitFor(() => expect(runNow).toHaveBeenCalledWith("roadmap"));
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("Roadmap Cycle cycle opened"),
    );
  });

  it("toggles the enabled switch via the settings mutation", async () => {
    render(withQueryClient(<BoardProgramsCard />));
    resolveListRef.current?.([buildProgram({ enabled: true })]);

    const toggle = await screen.findByRole("switch");
    fireEvent.click(toggle);

    await waitFor(() =>
      expect(setFeatureFlag).toHaveBeenCalledWith(
        "board_program.roadmap.enabled",
        false,
      ),
    );
  });

  it("does not fetch cycle history until the History dialog is opened", async () => {
    render(withQueryClient(<BoardProgramsCard />));
    resolveListRef.current?.([buildProgram()]);
    await screen.findByText("roadmap");

    expect(cycles).not.toHaveBeenCalled();
  });

  it("opens the History dialog and renders a past cycle's decisions", async () => {
    cycles.mockResolvedValueOnce([buildCycle()]);
    render(withQueryClient(<BoardProgramsCard />));
    resolveListRef.current?.([buildProgram()]);
    await screen.findByText("roadmap");

    fireEvent.click(screen.getByRole("button", { name: /History/i }));

    await waitFor(() => expect(cycles).toHaveBeenCalledWith("roadmap"));
    expect(
      await screen.findByText("README claims a dead feature"),
    ).toBeInTheDocument();
    expect(screen.getByText("approved")).toBeInTheDocument();
  });

  it("shows an empty state when a program has no recorded cycles", async () => {
    cycles.mockResolvedValueOnce([]);
    render(withQueryClient(<BoardProgramsCard />));
    resolveListRef.current?.([buildProgram()]);
    await screen.findByText("roadmap");

    fireEvent.click(screen.getByRole("button", { name: /History/i }));

    expect(
      await screen.findByText("No cycles recorded yet."),
    ).toBeInTheDocument();
  });
});
