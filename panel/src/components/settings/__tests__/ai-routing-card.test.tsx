import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

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
  },
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

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
});
