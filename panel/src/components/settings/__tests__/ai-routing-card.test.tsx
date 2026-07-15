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
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders both key cards and the self-hosted section as one two-column band, Grok+Ollama stacked left", async () => {
    render(withQueryClient(<AIRoutingCard />));
    await screen.findByText("Grok (xAI) API key");

    const grokSection = screen.getByText("Grok (xAI) API key").closest("section");
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

  it("groups the per-agent override list by org structure with two-column rows inside each group", async () => {
    render(withQueryClient(<AIRoutingCard />));
    await screen.findByText("Per-agent override (mix mode)");

    for (const title of [
      "Board",
      "Main PM",
      "Backend Cell",
      "Frontend Cell",
      "UX/UI Cell",
      "Intake / Secretary",
    ]) {
      expect(
        screen.getByRole("heading", { level: 4, name: title }),
      ).toBeInTheDocument();
    }

    // Spot-check a row from each end of the org chart.
    expect(screen.getByText("product-owner")).toBeInTheDocument();
    expect(screen.getByText("be-dev-1")).toBeInTheDocument();
    expect(screen.getByText("secretary-1")).toBeInTheDocument();

    // The Backend Cell group renders its 5 rows inside a 2-column grid.
    const backendHeader = screen.getByRole("heading", {
      level: 4,
      name: "Backend Cell",
    });
    const rowGrid = backendHeader.nextElementSibling as HTMLElement;
    expect(rowGrid.className).toContain("sm:grid-cols-2");
    expect(rowGrid.querySelectorAll(":scope > div")).toHaveLength(5);

    // All 20 agents still render exactly one override select each.
    const mixSection = screen
      .getByText("Per-agent override (mix mode)")
      .closest("section")!;
    expect(
      mixSection.querySelectorAll('[role="combobox"]'),
    ).toHaveLength(20);
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
