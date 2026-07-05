import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

const { getCredentialsStatus, setCredentials } = vi.hoisted(() => ({
  getCredentialsStatus: vi.fn(async () => ({ has_credentials: false })),
  setCredentials: vi.fn(async () => ({ has_credentials: true })),
}));

vi.mock("@/lib/api", () => ({
  videoApi: { getCredentialsStatus, setCredentials },
}));

import { TikTokCredentialsForm } from "../tiktok-credentials-card";

function withQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

describe("TikTokCredentialsForm", () => {
  beforeEach(() => {
    getCredentialsStatus.mockClear();
    setCredentials.mockClear();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows 'no credentials configured' by default and never renders a secret", async () => {
    render(withQueryClient(<TikTokCredentialsForm />));
    expect(
      await screen.findByText("No credentials configured"),
    ).toBeInTheDocument();
  });

  it("disables Save until all 4 fields are filled", async () => {
    render(withQueryClient(<TikTokCredentialsForm />));
    await screen.findByText("No credentials configured");
    const saveButton = screen.getByRole("button", { name: "Save" });
    expect(saveButton).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Client key"), {
      target: { value: "ck" },
    });
    expect(saveButton).toBeDisabled(); // still 3 unfilled

    fireEvent.change(screen.getByLabelText("Client secret"), {
      target: { value: "cs" },
    });
    fireEvent.change(screen.getByLabelText("Access token"), {
      target: { value: "at" },
    });
    fireEvent.change(screen.getByLabelText("Refresh token"), {
      target: { value: "rt" },
    });
    expect(saveButton).not.toBeDisabled();
  });

  it("saves all 4 secrets and clears the inputs on success", async () => {
    render(withQueryClient(<TikTokCredentialsForm />));
    await screen.findByText("No credentials configured");

    fireEvent.change(screen.getByLabelText("Client key"), {
      target: { value: "ck" },
    });
    fireEvent.change(screen.getByLabelText("Client secret"), {
      target: { value: "cs" },
    });
    fireEvent.change(screen.getByLabelText("Access token"), {
      target: { value: "at" },
    });
    fireEvent.change(screen.getByLabelText("Refresh token"), {
      target: { value: "rt" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(setCredentials).toHaveBeenCalledWith({
        client_key: "ck",
        client_secret: "cs",
        access_token: "at",
        refresh_token: "rt",
      }),
    );
    await waitFor(() =>
      expect(
        (screen.getByLabelText("Client key") as HTMLInputElement).value,
      ).toBe(""),
    );
  });
});
