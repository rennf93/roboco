import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import type { BoardProgram } from "@/lib/api/board-programs";

const { resolveListRef } = vi.hoisted(() => ({
  resolveListRef: { current: null as null | ((v: unknown) => void) },
}));

function buildProgram(overrides: Partial<BoardProgram> = {}): BoardProgram {
  return {
    key: "roadmap",
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

const { list, runNow } = vi.hoisted(() => ({
  list: vi.fn(
    () =>
      new Promise((r) => {
        resolveListRef.current = r as (v: unknown) => void;
      }),
  ),
  runNow: vi.fn(async () => buildProgram({ open_cycle: true })),
}));

vi.mock("@/lib/api/board-programs", () => ({
  boardProgramsApi: { list, runNow },
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
      expect(toast.success).toHaveBeenCalledWith("roadmap cycle opened"),
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
});
