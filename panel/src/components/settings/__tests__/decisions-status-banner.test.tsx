import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

const { useDecisionsStatus } = vi.hoisted(() => ({
  useDecisionsStatus: vi.fn(),
}));

vi.mock("@/hooks/use-providers", () => ({ useDecisionsStatus }));

import { DecisionsStatusBanner } from "../decisions-status-banner";

function withQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

function status(overrides: Partial<{
  decisions_enabled: boolean;
  openrouter_opted_in: boolean;
  openrouter_key_present: boolean;
}> = {}) {
  return {
    decisions_enabled: true,
    openrouter_opted_in: false,
    openrouter_key_present: false,
    ...overrides,
  };
}

describe("DecisionsStatusBanner", () => {
  beforeEach(() => {
    useDecisionsStatus.mockClear();
    // Default: no Decisions opt-in anywhere - the everyday panel state.
    useDecisionsStatus.mockReturnValue({ data: status() });
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders the warning when the OpenRouter tier is opted in but no key is stored", () => {
    useDecisionsStatus.mockReturnValue({
      data: status({ openrouter_opted_in: true, openrouter_key_present: false }),
    });
    render(withQueryClient(<DecisionsStatusBanner />));

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/openrouter fallback tier is opted in/i);
    expect(alert).toHaveTextContent(/no openrouter api key is configured/i);
  });

  it("stays hidden when the tier is not opted in", () => {
    useDecisionsStatus.mockReturnValue({
      data: status({ openrouter_opted_in: false, openrouter_key_present: false }),
    });
    render(withQueryClient(<DecisionsStatusBanner />));

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("stays hidden when the tier is opted in AND a key is present", () => {
    useDecisionsStatus.mockReturnValue({
      data: status({ openrouter_opted_in: true, openrouter_key_present: true }),
    });
    render(withQueryClient(<DecisionsStatusBanner />));

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("stays hidden while the status query has not loaded", () => {
    useDecisionsStatus.mockReturnValue({ data: undefined });
    render(withQueryClient(<DecisionsStatusBanner />));

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("notes when the Decisions master flag is off alongside the missing key", () => {
    useDecisionsStatus.mockReturnValue({
      data: status({
        decisions_enabled: false,
        openrouter_opted_in: true,
        openrouter_key_present: false,
      }),
    });
    render(withQueryClient(<DecisionsStatusBanner />));

    expect(screen.getByRole("alert")).toHaveTextContent(
      /master flag is currently off/i,
    );
  });
});
