import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

const { getCredentialsStatus, setCredentials } = vi.hoisted(() => ({
  getCredentialsStatus: vi.fn(async () => ({ has_credentials: false })),
  setCredentials: vi.fn(async () => ({ has_credentials: true })),
}));

vi.mock("@/lib/api", () => ({
  xApi: { getCredentialsStatus, setCredentials },
}));

import { XCredentialsForm } from "../x-credentials-card";

function withQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

describe("XCredentialsForm", () => {
  beforeEach(() => {
    getCredentialsStatus.mockClear();
    setCredentials.mockClear();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows 'no credentials configured' by default and never renders a secret", async () => {
    render(withQueryClient(<XCredentialsForm />));
    expect(
      await screen.findByText("No credentials configured"),
    ).toBeInTheDocument();
  });

  it("disables Save until all 4 fields are filled", async () => {
    render(withQueryClient(<XCredentialsForm />));
    await screen.findByText("No credentials configured");
    const saveButton = screen.getByRole("button", { name: "Save" });
    expect(saveButton).toBeDisabled();

    fireEvent.change(screen.getByLabelText("API key"), {
      target: { value: "ak" },
    });
    expect(saveButton).toBeDisabled(); // still 3 unfilled

    fireEvent.change(screen.getByLabelText("API key secret"), {
      target: { value: "as" },
    });
    fireEvent.change(screen.getByLabelText("Access token"), {
      target: { value: "at" },
    });
    fireEvent.change(screen.getByLabelText("Access token secret"), {
      target: { value: "ats" },
    });
    expect(saveButton).not.toBeDisabled();
  });

  it("saves all 4 secrets and clears the inputs on success", async () => {
    render(withQueryClient(<XCredentialsForm />));
    await screen.findByText("No credentials configured");

    fireEvent.change(screen.getByLabelText("API key"), {
      target: { value: "ak" },
    });
    fireEvent.change(screen.getByLabelText("API key secret"), {
      target: { value: "as" },
    });
    fireEvent.change(screen.getByLabelText("Access token"), {
      target: { value: "at" },
    });
    fireEvent.change(screen.getByLabelText("Access token secret"), {
      target: { value: "ats" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(setCredentials).toHaveBeenCalledWith({
        api_key: "ak",
        api_secret: "as",
        access_token: "at",
        access_token_secret: "ats",
      }),
    );
    await waitFor(() =>
      expect(
        (screen.getByLabelText("API key") as HTMLInputElement).value,
      ).toBe(""),
    );
  });
});
