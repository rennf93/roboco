import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

const { getAll, update } = vi.hoisted(() => ({
  getAll: vi.fn(async () => ({}) as Record<string, string>),
  update: vi.fn(async () => ({}) as Record<string, string>),
}));

vi.mock("@/lib/api", () => ({
  settingsApi: { getAll, update },
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

// Functional Select mock (mirrors ai-routing-card.test.tsx): SelectItem
// renders as a clickable button wired to onValueChange via context, so the
// tri-state change is simulated without Radix's portal/pointer machinery.
vi.mock("@/components/ui/select", () => {
  const Ctx = React.createContext<{
    value?: string;
    onValueChange: (v: string) => void;
  }>({ onValueChange: () => {} });
  return {
    Select: ({
      value,
      onValueChange,
      children,
    }: {
      value?: string;
      onValueChange?: (v: string) => void;
      children: React.ReactNode;
    }) => (
      <Ctx.Provider value={{ value, onValueChange: onValueChange ?? (() => {}) }}>
        {children}
      </Ctx.Provider>
    ),
    SelectTrigger: ({ children }: { children: React.ReactNode }) => {
      const { value } = React.useContext(Ctx);
      return (
        <button
          type="button"
          role="combobox"
          aria-expanded={false}
          aria-controls="mock-select-content"
          data-value={value ?? ""}
        >
          {children}
        </button>
      );
    },
    SelectValue: () => null,
    SelectContent: ({ children }: { children: React.ReactNode }) => (
      <div>{children}</div>
    ),
    SelectItem: ({
      value,
      children,
    }: {
      value: string;
      children: React.ReactNode;
    }) => {
      const { onValueChange } = React.useContext(Ctx);
      return (
        <button
          type="button"
          role="option"
          aria-selected={false}
          onClick={() => onValueChange(value)}
        >
          {children}
        </button>
      );
    },
  };
});

import { DecisionsPilotsCard } from "../decisions-pilots-card";

function withQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

const PILOT_ROWS = [
  "Self-heal (transient-CI gate)",
  "Parking (rate-limit routing)",
  "Complexity (spawn-time routing)",
  "Preflight diff (agent lane)",
  "Triage failure (agent lane)",
  "Steer gate (A2A steering)",
  "Transcript notes (at finalize)",
];

describe("DecisionsPilotsCard", () => {
  beforeEach(() => {
    getAll.mockClear();
    update.mockClear();
    getAll.mockResolvedValue({});
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders one tri-state row per pilot (7 rows), all defaulting to off when unset", async () => {
    render(withQueryClient(<DecisionsPilotsCard />));

    for (const label of PILOT_ROWS) {
      expect(await screen.findByText(label)).toBeInTheDocument();
    }
    const triggers = await screen.findAllByRole("combobox");
    expect(triggers).toHaveLength(7);
    for (const trigger of triggers) {
      expect(trigger.getAttribute("data-value")).toBe("off");
    }
  });

  it("shows stored values from the settings map for dotted pilot keys", async () => {
    getAll.mockResolvedValue({
      "decisions.pilot.steer_gate": "shadow",
      "decisions.pilot.self_heal": "on",
    });
    render(withQueryClient(<DecisionsPilotsCard />));

    await waitFor(() => {
      const rows = screen.getAllByRole("combobox");
      const values = rows.map((r) => r.getAttribute("data-value"));
      expect(values.filter((v) => v === "shadow")).toHaveLength(1);
      expect(values.filter((v) => v === "on")).toHaveLength(1);
      expect(values.filter((v) => v === "off")).toHaveLength(5);
    });
  });

  it("a tri-state change calls the settings API with the dotted key and value", async () => {
    render(withQueryClient(<DecisionsPilotsCard />));

    const row = screen.getByTestId("pilot-row-self_heal");
    fireEvent.click(
      await within(row).findByRole("option", { name: "shadow" }),
    );

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith("decisions.pilot.self_heal", "shadow"),
    );
  });

  it("switching a pilot to on persists 'on' for its dotted key", async () => {
    render(withQueryClient(<DecisionsPilotsCard />));

    const row = screen.getByTestId("pilot-row-steer_gate");
    fireEvent.click(await within(row).findByRole("option", { name: "on" }));

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith("decisions.pilot.steer_gate", "on"),
    );
  });
});
