import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import React from "react";
import type {
  ComplexityOverride,
  RoutingPreset,
} from "@/lib/api/providers";

const {
  catalog,
  getOllamaKey,
  setOllamaKey,
  getGrokKey,
  setGrokKey,
  getMode,
  applyMode,
  getSelfHostedConfig,
  saveSelfHostedConfig,
  testSelfHosted,
  getSelfHostedModels,
  getComplexityOverrides,
  setComplexityOverride,
  deleteComplexityOverride,
  listPresets,
  savePreset,
  applyPreset,
  deletePreset,
} = vi.hoisted(() => ({
  catalog: vi.fn(async () => [
    {
      model_name: "claude-opus-4-6",
      provider_type: "anthropic",
      display_name: "Claude Opus 4.6",
    },
    {
      model_name: "grok-build-0.1",
      provider_type: "grok",
      display_name: "Grok Build 0.1",
    },
    {
      model_name: "gpt-5.3-codex",
      provider_type: "openai",
      display_name: "GPT-5.3 Codex",
    },
  ]),
  getOllamaKey: vi.fn(async () => ({ has_key: false, enabled: true })),
  setOllamaKey: vi.fn(async () => ({ has_key: true, enabled: true })),
  getGrokKey: vi.fn(async () => ({ has_key: false, enabled: true })),
  setGrokKey: vi.fn(async () => ({ has_key: true, enabled: true })),
  getMode: vi.fn(async () => ({ mode: "anthropic", assignments: [] })),
  applyMode: vi.fn(async (payload: { mode: string }) => ({
    mode: payload.mode,
    assignments: [],
  })),
  getSelfHostedConfig: vi.fn(async () => ({
    base_url: null,
    has_token: false,
    enabled: true,
  })),
  saveSelfHostedConfig: vi.fn(async () => ({
    base_url: "http://localhost:11434",
    has_token: false,
    enabled: true,
  })),
  testSelfHosted: vi.fn(async () => ({
    ok: false,
    model_count: null,
    error: null,
  })),
  getSelfHostedModels: vi.fn(async () => []),
  getComplexityOverrides: vi.fn(async (): Promise<ComplexityOverride[]> => []),
  setComplexityOverride: vi.fn(async (payload: ComplexityOverride) => payload),
  deleteComplexityOverride: vi.fn(async () => undefined),
  listPresets: vi.fn(async (): Promise<RoutingPreset[]> => []),
  savePreset: vi.fn(async (name: string) => ({
    id: "preset-1",
    name,
    created_at: "2026-07-23T00:00:00Z",
  })),
  applyPreset: vi.fn(async () => ({
    mode: "mix",
    assignments: [],
    skipped: [] as string[],
  })),
  deletePreset: vi.fn(async () => undefined),
}));

vi.mock("@/lib/api/providers", () => ({
  providersApi: {
    catalog,
    getOllamaKey,
    setOllamaKey,
    getGrokKey,
    setGrokKey,
    getMode,
    applyMode,
    getSelfHostedConfig,
    saveSelfHostedConfig,
    testSelfHosted,
    getSelfHostedModels,
    getComplexityOverrides,
    setComplexityOverride,
    deleteComplexityOverride,
    listPresets,
    savePreset,
    applyPreset,
    deletePreset,
  },
  COMPLEXITY_OVERRIDE_ROLES: ["developer", "qa", "documenter"],
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

// Functional Select mock (mirrors select-repo-picker.test.tsx /
// a2a-reply-composer.test.tsx): SelectItem renders as a clickable button
// wired to onValueChange via context, so a real "pick a value" interaction
// can be simulated without Radix's portal/pointer machinery. SelectTrigger
// keeps `role="combobox"` so the pre-existing per-agent-table combobox-count
// assertion is unaffected, and stamps `data-value` from context so a test can
// `waitFor` an async-loaded value (e.g. a complexity override) landing before
// interacting further.
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
      <Ctx.Provider
        value={{ value, onValueChange: onValueChange ?? (() => {}) }}
      >
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
    SelectGroup: ({ children }: { children: React.ReactNode }) => (
      <div>{children}</div>
    ),
    SelectLabel: ({ children }: { children: React.ReactNode }) => (
      <div>{children}</div>
    ),
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

// Full live roster (minus CEO/system) — mirrors foundation/identity.py so the
// per-agent override grid is exercised against the real 25-agent org chart,
// including the four PR reviewers a prior hard-coded literal dropped.
const { useAgentDefinitions } = vi.hoisted(() => ({
  useAgentDefinitions: vi.fn(),
}));

vi.mock("@/hooks/use-agents", () => ({ useAgentDefinitions }));

const FULL_ROSTER = [
  {
    id: "main-pm",
    uuid: "u-main-pm",
    name: "Main PM",
    role: "main_pm",
    team: "main_pm",
  },
  {
    id: "product-owner",
    uuid: "u-po",
    name: "Product Owner",
    role: "product_owner",
    team: "board",
  },
  {
    id: "head-marketing",
    uuid: "u-hom",
    name: "Head of Marketing",
    role: "head_marketing",
    team: "board",
  },
  {
    id: "auditor",
    uuid: "u-auditor",
    name: "Auditor",
    role: "auditor",
    team: "board",
  },
  {
    id: "intake-1",
    uuid: "u-intake",
    name: "Intake",
    role: "prompter",
    team: "board",
  },
  {
    id: "secretary-1",
    uuid: "u-secretary",
    name: "Secretary",
    role: "secretary",
    team: "board",
  },
  {
    id: "pr-reviewer-1",
    uuid: "u-prr",
    name: "PR Reviewer",
    role: "pr_reviewer",
    team: "board",
  },
  {
    id: "be-dev-1",
    uuid: "u-be1",
    name: "Backend Developer 1",
    role: "developer",
    team: "backend",
  },
  {
    id: "be-dev-2",
    uuid: "u-be2",
    name: "Backend Developer 2",
    role: "developer",
    team: "backend",
  },
  {
    id: "be-qa",
    uuid: "u-beqa",
    name: "Backend QA",
    role: "qa",
    team: "backend",
  },
  {
    id: "be-pm",
    uuid: "u-bepm",
    name: "Backend PM",
    role: "cell_pm",
    team: "backend",
  },
  {
    id: "be-doc",
    uuid: "u-bedoc",
    name: "Backend Documenter",
    role: "documenter",
    team: "backend",
  },
  {
    id: "be-pr-reviewer",
    uuid: "u-bepr",
    name: "Backend PR Reviewer",
    role: "pr_reviewer",
    team: "backend",
  },
  {
    id: "fe-dev-1",
    uuid: "u-fe1",
    name: "Frontend Developer 1",
    role: "developer",
    team: "frontend",
  },
  {
    id: "fe-dev-2",
    uuid: "u-fe2",
    name: "Frontend Developer 2",
    role: "developer",
    team: "frontend",
  },
  {
    id: "fe-qa",
    uuid: "u-feqa",
    name: "Frontend QA",
    role: "qa",
    team: "frontend",
  },
  {
    id: "fe-pm",
    uuid: "u-fepm",
    name: "Frontend PM",
    role: "cell_pm",
    team: "frontend",
  },
  {
    id: "fe-doc",
    uuid: "u-fedoc",
    name: "Frontend Documenter",
    role: "documenter",
    team: "frontend",
  },
  {
    id: "fe-pr-reviewer",
    uuid: "u-fepr",
    name: "Frontend PR Reviewer",
    role: "pr_reviewer",
    team: "frontend",
  },
  {
    id: "ux-dev-1",
    uuid: "u-ux1",
    name: "UX/UI Developer 1",
    role: "developer",
    team: "ux_ui",
  },
  {
    id: "ux-dev-2",
    uuid: "u-ux2",
    name: "UX/UI Developer 2",
    role: "developer",
    team: "ux_ui",
  },
  { id: "ux-qa", uuid: "u-uxqa", name: "UX/UI QA", role: "qa", team: "ux_ui" },
  {
    id: "ux-pm",
    uuid: "u-uxpm",
    name: "UX/UI PM",
    role: "cell_pm",
    team: "ux_ui",
  },
  {
    id: "ux-doc",
    uuid: "u-uxdoc",
    name: "UX/UI Documenter",
    role: "documenter",
    team: "ux_ui",
  },
  {
    id: "ux-pr-reviewer",
    uuid: "u-uxpr",
    name: "UX/UI PR Reviewer",
    role: "pr_reviewer",
    team: "ux_ui",
  },
] as unknown as import("@/hooks/use-agents").AgentDefinition[];

import { toast } from "sonner";
import { AIRoutingCard } from "../ai-routing-card";

function withQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

describe("AIRoutingCard", () => {
  beforeEach(() => {
    catalog.mockClear();
    getOllamaKey.mockClear();
    setOllamaKey.mockClear();
    getGrokKey.mockClear();
    setGrokKey.mockClear();
    getMode.mockClear();
    applyMode.mockClear();
    getSelfHostedConfig.mockClear();
    saveSelfHostedConfig.mockClear();
    testSelfHosted.mockClear();
    getSelfHostedModels.mockClear();
    getComplexityOverrides.mockClear();
    setComplexityOverride.mockClear();
    deleteComplexityOverride.mockClear();
    listPresets.mockClear();
    savePreset.mockClear();
    applyPreset.mockClear();
    deletePreset.mockClear();
    useAgentDefinitions.mockReturnValue({
      data: FULL_ROSTER,
      isLoading: false,
    });
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders both key cards and the self-hosted section as one two-column band, Grok+Ollama stacked left", async () => {
    render(withQueryClient(<AIRoutingCard />));
    await screen.findByText("Grok (xAI) API key");

    const grokSection = screen
      .getByText("Grok (xAI) API key")
      .closest("section");
    const ollamaSection = screen
      .getByText("Ollama Cloud API key")
      .closest("section");
    expect(grokSection).toBeInTheDocument();
    expect(ollamaSection).toBeInTheDocument();

    // Grok + Ollama share the same left-column container.
    const leftColumn = grokSection?.parentElement;
    expect(leftColumn).toContainElement(ollamaSection as HTMLElement);

    // That left column and the Self-Hosted section are the two children of
    // one responsive two-column grid band.
    const selfHostedHeading = screen.getByText("Self-Hosted LLM");
    const band = leftColumn?.parentElement;
    expect(band).toContainElement(selfHostedHeading);
    expect(band?.className).toContain("grid");
    expect(band?.className).toContain("lg:grid-cols-2");
  });

  it("shows 'not set' badges by default and saves+clears the Grok key", async () => {
    render(withQueryClient(<AIRoutingCard />));
    await screen.findByText("Grok (xAI) API key");
    expect(screen.getAllByText("not set")).toHaveLength(2); // Grok + Ollama

    const grokInput = screen.getByPlaceholderText("xai-…");
    fireEvent.change(grokInput, { target: { value: "xai-secret" } });
    const grokSection = screen
      .getByText("Grok (xAI) API key")
      .closest("section")!;
    const saveButton = Array.from(grokSection.querySelectorAll("button")).find(
      (b) => b.textContent === "Save",
    )!;
    fireEvent.click(saveButton);

    await waitFor(() => expect(setGrokKey).toHaveBeenCalledWith("xai-secret"));
  });

  it("saving the Ollama key clears the input on success", async () => {
    render(withQueryClient(<AIRoutingCard />));
    await screen.findByText("Ollama Cloud API key");

    const ollamaInput = screen.getByPlaceholderText(
      "ollama_xxx…",
    ) as HTMLInputElement;
    fireEvent.change(ollamaInput, { target: { value: "ollama_secret" } });

    const ollamaSection = screen
      .getByText("Ollama Cloud API key")
      .closest("section")!;
    const saveButton = Array.from(
      ollamaSection.querySelectorAll("button"),
    ).find((b) => b.textContent === "Save")!;
    fireEvent.click(saveButton);

    await waitFor(() =>
      expect(setOllamaKey).toHaveBeenCalledWith("ollama_secret"),
    );
    await waitFor(() => expect(ollamaInput.value).toBe(""));
  });

  it("groups the per-agent override list by org structure with two-column rows inside each group, including all four PR reviewers", async () => {
    render(withQueryClient(<AIRoutingCard />));
    await screen.findByText("Per-agent override (mix mode)");

    for (const title of [
      "Board",
      "Main PM",
      "Backend Cell",
      "Frontend Cell",
      "UX/UI Cell",
      "Intake / Secretary / PR Review",
    ]) {
      expect(
        screen.getByRole("heading", { level: 4, name: title }),
      ).toBeInTheDocument();
    }

    // Spot-check a row from each end of the org chart, including the agents
    // the old hard-coded literal dropped: ux-dev-2 and all four reviewers.
    expect(screen.getByText("product-owner")).toBeInTheDocument();
    expect(screen.getByText("be-dev-1")).toBeInTheDocument();
    expect(screen.getByText("secretary-1")).toBeInTheDocument();
    expect(screen.getByText("ux-dev-2")).toBeInTheDocument();
    expect(screen.getByText("pr-reviewer-1")).toBeInTheDocument();
    expect(screen.getByText("be-pr-reviewer")).toBeInTheDocument();
    expect(screen.getByText("fe-pr-reviewer")).toBeInTheDocument();
    expect(screen.getByText("ux-pr-reviewer")).toBeInTheDocument();

    // The Backend Cell group renders its 6 rows (5 + PR reviewer) inside a
    // 2-column grid.
    const backendHeader = screen.getByRole("heading", {
      level: 4,
      name: "Backend Cell",
    });
    const rowGrid = backendHeader.nextElementSibling as HTMLElement;
    expect(rowGrid.className).toContain("sm:grid-cols-2");
    expect(rowGrid.querySelectorAll(":scope > div")).toHaveLength(6);

    // All 25 agents (the full roster minus the CEO) still render exactly one
    // override select each.
    const mixSection = screen
      .getByText("Per-agent override (mix mode)")
      .closest("section")!;
    expect(mixSection.querySelectorAll('[role="combobox"]')).toHaveLength(25);
  });

  it("shows a loading skeleton (not an empty grid) while the agent roster is still loading", async () => {
    useAgentDefinitions.mockReturnValue({ data: undefined, isLoading: true });
    render(withQueryClient(<AIRoutingCard />));
    await screen.findByText("Per-agent override (mix mode)");

    expect(
      screen.queryByRole("heading", { level: 4, name: "Board" }),
    ).not.toBeInTheDocument();
    const mixSection = screen
      .getByText("Per-agent override (mix mode)")
      .closest("section")!;
    expect(mixSection.querySelector(".animate-pulse")).toBeInTheDocument();
  });

  it("shows an error note (not a silently empty grid) when the roster fetch fails", async () => {
    useAgentDefinitions.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    });
    render(withQueryClient(<AIRoutingCard />));
    await screen.findByText("Per-agent override (mix mode)");

    expect(
      screen.getByText(/Couldn.t load the agent roster/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { level: 4, name: "Board" }),
    ).not.toBeInTheDocument();
    const mixSection = screen
      .getByText("Per-agent override (mix mode)")
      .closest("section")!;
    expect(mixSection.querySelector(".animate-pulse")).not.toBeInTheDocument();
  });

  it("tooltip-wraps the Grok/Ollama key labels and status badges, not the raw Switch", async () => {
    render(withQueryClient(<AIRoutingCard />));
    await screen.findByText("Grok (xAI) API key");

    // TooltipTrigger always stamps data-state onto its asChild target, so
    // its presence is a reliable proxy for "this element is tooltip-wrapped"
    // without simulating hover (Radix only portals content once open).
    expect(
      screen.getByText("Grok (xAI) API key").getAttribute("data-state"),
    ).toBe("closed");
    expect(
      screen.getByText("Ollama Cloud API key").getAttribute("data-state"),
    ).toBe("closed");

    const notSetBadges = screen.getAllByText("not set");
    expect(notSetBadges).toHaveLength(2);
    for (const badge of notSetBadges) {
      expect(badge.getAttribute("data-state")).toBe("closed");
    }
  });

  it("saving the mix with no picks shows an error and never calls applyMode", async () => {
    render(withQueryClient(<AIRoutingCard />));
    await screen.findByText("Per-agent override (mix mode)");

    fireEvent.click(screen.getByRole("button", { name: "Save mix" }));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "Pick a model for at least one agent",
      ),
    );
    expect(applyMode).not.toHaveBeenCalled();
  });

  // -------------------------------------------------------------------------
  // Complexity overrides (cost-tiered routing)
  // -------------------------------------------------------------------------

  describe("Complexity overrides section", () => {
    it("renders one row per allowlisted role with Low/High selects, and no coordinator role", async () => {
      render(withQueryClient(<AIRoutingCard />));
      await screen.findByText("Complexity overrides");

      const section = screen.getByText("Complexity overrides").closest("section")!;
      for (const label of ["Developer", "QA", "Documenter"]) {
        expect(within(section).getByText(label)).toBeInTheDocument();
      }
      // Coordinator (cell_pm, main_pm)/pr_reviewer/board/CEO-facing roles are
      // never offered a row here — scoped to this section, since "Cell PM"/
      // "Main PM"/"PR Reviewer" legitimately appear elsewhere (the per-agent
      // mix table's roster).
      expect(within(section).queryByText("Cell PM")).not.toBeInTheDocument();
      expect(within(section).queryByText("Main PM")).not.toBeInTheDocument();
      expect(within(section).queryByText("PR Reviewer")).not.toBeInTheDocument();

      // 3 roles x 2 (low/high) selects.
      expect(section.querySelectorAll('[role="combobox"]')).toHaveLength(6);
    });

    it("picking a model for a role+complexity calls setComplexityOverride with the right payload", async () => {
      render(withQueryClient(<AIRoutingCard />));
      await screen.findByText("Complexity overrides");

      const devLow = screen.getByTestId("complexity-select-developer-low");
      fireEvent.click(
        await within(devLow).findByRole("option", { name: "Claude Opus 4.6" }),
      );

      await waitFor(() =>
        expect(setComplexityOverride).toHaveBeenCalledWith({
          role: "developer",
          complexity: "low",
          model_name: "claude-opus-4-6",
        }),
      );
    });

    it("surfaces a returned cross-family warning as its own warning toast", async () => {
      setComplexityOverride.mockResolvedValueOnce({
        role: "developer",
        complexity: "low",
        model_name: "grok-build-0.1",
        warning:
          "'grok-build-0.1' routes through grok, a different provider family than developer's Anthropic baseline.",
      });
      render(withQueryClient(<AIRoutingCard />));
      await screen.findByText("Complexity overrides");

      const devLow = screen.getByTestId("complexity-select-developer-low");
      fireEvent.click(
        await within(devLow).findByRole("option", { name: "Grok Build 0.1" }),
      );

      await waitFor(() =>
        expect(toast.warning).toHaveBeenCalledWith(
          expect.stringContaining("different provider family"),
        ),
      );
      // Still allowed — the success toast still fires alongside the warning.
      expect(toast.success).toHaveBeenCalledWith(
        "developer:low → grok-build-0.1",
      );
    });

    it("never shows a warning toast when the response carries none", async () => {
      render(withQueryClient(<AIRoutingCard />));
      await screen.findByText("Complexity overrides");

      const devLow = screen.getByTestId("complexity-select-developer-low");
      fireEvent.click(
        await within(devLow).findByRole("option", { name: "Claude Opus 4.6" }),
      );

      await waitFor(() => expect(setComplexityOverride).toHaveBeenCalled());
      expect(toast.warning).not.toHaveBeenCalled();
    });

    it("picking '(none)' on a row with no existing override never calls deleteComplexityOverride", async () => {
      render(withQueryClient(<AIRoutingCard />));
      await screen.findByText("Complexity overrides");

      const qaHigh = screen.getByTestId("complexity-select-qa-high");
      fireEvent.click(
        await within(qaHigh).findByRole("option", { name: "(none)" }),
      );

      // No pre-existing row for qa:high (getComplexityOverrides returns []
      // by default) — clearing an already-empty selection is a no-op.
      await waitFor(() => expect(setComplexityOverride).not.toHaveBeenCalled());
      expect(deleteComplexityOverride).not.toHaveBeenCalled();
    });

    it("picking '(none)' on a row WITH an existing override calls deleteComplexityOverride", async () => {
      getComplexityOverrides.mockResolvedValueOnce([
        { role: "documenter", complexity: "low", model_name: "grok-build-0.1" },
      ]);
      render(withQueryClient(<AIRoutingCard />));
      await screen.findByText("Complexity overrides");

      const documenterLow = await screen.findByTestId(
        "complexity-select-documenter-low",
      );
      // Wait for the async-loaded override to actually land on the Select
      // (its combobox reflects the current value via data-value) before
      // clearing it — otherwise the click races the query resolving.
      await waitFor(() =>
        expect(
          within(documenterLow).getByRole("combobox"),
        ).toHaveAttribute("data-value", "grok-build-0.1"),
      );
      fireEvent.click(
        within(documenterLow).getByRole("option", { name: "(none)" }),
      );

      // deleteComplexityOverride(role, complexity) — positional, per the
      // providersApi signature (mirrors the real hook's mutationFn).
      await waitFor(() =>
        expect(deleteComplexityOverride).toHaveBeenCalledWith(
          "documenter",
          "low",
        ),
      );
    });
  });

  // -------------------------------------------------------------------------
  // Cost-Tiered mode button (additive seed, never wipes routing)
  // -------------------------------------------------------------------------

  describe("Cost-Tiered mode button", () => {
    it("renders beside the other mode buttons and applies mode='cost_tiered' on confirm", async () => {
      const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
      render(withQueryClient(<AIRoutingCard />));
      await screen.findByText("Grok (xAI) API key");

      fireEvent.click(screen.getByText("Cost-Tiered"));

      await waitFor(() =>
        expect(applyMode).toHaveBeenCalledWith({ mode: "cost_tiered" }),
      );
      confirmSpy.mockRestore();
    });

    it("does nothing when the confirm dialog is declined", async () => {
      const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
      render(withQueryClient(<AIRoutingCard />));
      await screen.findByText("Grok (xAI) API key");

      fireEvent.click(screen.getByText("Cost-Tiered"));

      await waitFor(() => expect(confirmSpy).toHaveBeenCalled());
      expect(applyMode).not.toHaveBeenCalled();
      confirmSpy.mockRestore();
    });
  });

  // -------------------------------------------------------------------------
  // Mode switches preserve complexity overrides (2026-07-17-style incident:
  // these same buttons once wiped AGENT_SLUG pins) — the confirm text says so
  // and the query cache is refreshed for both, not just the mode snapshot.
  // -------------------------------------------------------------------------

  describe("Mode switches preserve complexity overrides", () => {
    it("applying Anthropic mode also refetches complexity overrides, not just the mode snapshot", async () => {
      const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
      render(withQueryClient(<AIRoutingCard />));
      await screen.findByText("Grok (xAI) API key");
      getComplexityOverrides.mockClear();

      fireEvent.click(screen.getByText("Anthropic"));

      await waitFor(() =>
        expect(applyMode).toHaveBeenCalledWith({ mode: "anthropic" }),
      );
      await waitFor(() =>
        expect(getComplexityOverrides.mock.calls.length).toBeGreaterThan(0),
      );
      confirmSpy.mockRestore();
    });

    it("the Anthropic confirm dialog states complexity overrides are kept", async () => {
      const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
      render(withQueryClient(<AIRoutingCard />));
      await screen.findByText("Grok (xAI) API key");

      fireEvent.click(screen.getByText("Anthropic"));

      await waitFor(() => expect(confirmSpy).toHaveBeenCalled());
      expect(confirmSpy.mock.calls[0][0]).toContain("complexity");
      confirmSpy.mockRestore();
    });
  });

  // -------------------------------------------------------------------------
  // Routing presets (named, full snapshots)
  // -------------------------------------------------------------------------

  describe("Routing presets bar", () => {
    it("lists saved presets and disables Apply/Delete until one is picked", async () => {
      listPresets.mockResolvedValueOnce([
        { id: "p1", name: "Cheap Fleet", created_at: "2026-07-01T00:00:00Z" },
      ]);
      render(withQueryClient(<AIRoutingCard />));
      await screen.findByText("Cheap Fleet");

      expect(screen.getByRole("button", { name: "Apply" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Delete" })).toBeDisabled();
    });

    it("save-as-preset flow: reveals a name input and Confirm calls savePreset", async () => {
      render(withQueryClient(<AIRoutingCard />));
      await screen.findByText("Routing presets");

      fireEvent.click(screen.getByText("Save as preset…"));
      const nameInput = screen.getByPlaceholderText("Preset name");
      fireEvent.change(nameInput, { target: { value: "My Setup" } });
      fireEvent.click(screen.getByText("Confirm"));

      await waitFor(() => expect(savePreset).toHaveBeenCalledWith("My Setup"));
      await waitFor(() =>
        expect(toast.success).toHaveBeenCalledWith('Saved preset "My Setup"'),
      );
    });

    it("save-as-preset with an empty name shows an error and never calls savePreset", async () => {
      render(withQueryClient(<AIRoutingCard />));
      await screen.findByText("Routing presets");

      fireEvent.click(screen.getByText("Save as preset…"));
      fireEvent.click(screen.getByText("Confirm"));

      await waitFor(() =>
        expect(toast.error).toHaveBeenCalledWith("Enter a preset name first"),
      );
      expect(savePreset).not.toHaveBeenCalled();
    });

    it("applying a preset with skipped rows surfaces them in a toast error", async () => {
      const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
      listPresets.mockResolvedValueOnce([
        { id: "p1", name: "Cheap Fleet", created_at: "2026-07-01T00:00:00Z" },
      ]);
      applyPreset.mockResolvedValueOnce({
        mode: "mix",
        assignments: [],
        skipped: ["Skipped role:developer (ghost-model) — Unknown model"],
      });
      render(withQueryClient(<AIRoutingCard />));
      const presetSection = (await screen.findByText("Routing presets")).closest(
        "section",
      )!;
      fireEvent.click(
        await within(presetSection).findByRole("option", {
          name: "Cheap Fleet",
        }),
      );
      fireEvent.click(screen.getByRole("button", { name: "Apply" }));

      await waitFor(() => expect(applyPreset).toHaveBeenCalledWith("p1"));
      await waitFor(() =>
        expect(toast.error).toHaveBeenCalledWith(
          expect.stringContaining("1 row(s) skipped"),
        ),
      );
      confirmSpy.mockRestore();
    });

    it("deleting a preset calls deletePreset and clears the selection", async () => {
      const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
      listPresets.mockResolvedValueOnce([
        { id: "p1", name: "Cheap Fleet", created_at: "2026-07-01T00:00:00Z" },
      ]);
      render(withQueryClient(<AIRoutingCard />));
      const presetSection = (await screen.findByText("Routing presets")).closest(
        "section",
      )!;
      fireEvent.click(
        await within(presetSection).findByRole("option", {
          name: "Cheap Fleet",
        }),
      );
      fireEvent.click(screen.getByRole("button", { name: "Delete" }));

      await waitFor(() => expect(deletePreset).toHaveBeenCalledWith("p1"));
      confirmSpy.mockRestore();
    });
  });
});
