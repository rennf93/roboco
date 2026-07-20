import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

const { getCredentialsStatus, setCredentials, clearCredentials } = vi.hoisted(
  () => ({
    getCredentialsStatus: vi.fn(async () => ({ has_credentials: false })),
    setCredentials: vi.fn(async () => ({ has_credentials: true })),
    clearCredentials: vi.fn(async () => ({ has_credentials: false })),
  }),
);

vi.mock("@/lib/api", () => ({
  githubAppApi: { getCredentialsStatus, setCredentials, clearCredentials },
}));

import { GitHubAppCredentialsCard } from "../github-app-credentials-card";

function withQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

describe("GitHubAppCredentialsCard", () => {
  beforeEach(() => {
    getCredentialsStatus.mockClear();
    setCredentials.mockClear();
    clearCredentials.mockClear();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows 'no credentials configured' by default and no Clear button", async () => {
    render(withQueryClient(<GitHubAppCredentialsCard />));
    expect(
      await screen.findByText("No credentials configured"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Clear" }),
    ).not.toBeInTheDocument();
  });

  it("disables Save until both App id and private key are filled", async () => {
    render(withQueryClient(<GitHubAppCredentialsCard />));
    await screen.findByText("No credentials configured");
    const saveButton = screen.getByRole("button", { name: "Save" });
    expect(saveButton).toBeDisabled();

    fireEvent.change(screen.getByLabelText("App id"), {
      target: { value: "123456" },
    });
    expect(saveButton).toBeDisabled(); // private key still unfilled

    fireEvent.change(screen.getByLabelText("Private key (PEM)"), {
      target: { value: "-----BEGIN KEY-----" },
    });
    expect(saveButton).not.toBeDisabled();
  });

  it("saves both fields and clears the inputs on success", async () => {
    render(withQueryClient(<GitHubAppCredentialsCard />));
    await screen.findByText("No credentials configured");

    fireEvent.change(screen.getByLabelText("App id"), {
      target: { value: "123456" },
    });
    fireEvent.change(screen.getByLabelText("Private key (PEM)"), {
      target: { value: "-----BEGIN KEY-----" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(setCredentials).toHaveBeenCalledWith({
        app_id: "123456",
        private_key: "-----BEGIN KEY-----",
      }),
    );
    await waitFor(() =>
      expect((screen.getByLabelText("App id") as HTMLInputElement).value).toBe(
        "",
      ),
    );
  });

  it("shows a Clear button once credentials are set, gated behind a confirm dialog", async () => {
    getCredentialsStatus.mockResolvedValueOnce({ has_credentials: true });
    render(withQueryClient(<GitHubAppCredentialsCard />));
    await screen.findByText("Credentials are set");

    const clearButton = screen.getByRole("button", { name: "Clear" });
    fireEvent.click(clearButton);

    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toBeInTheDocument();
    expect(clearCredentials).not.toHaveBeenCalled();

    fireEvent.click(
      screen.getAllByRole("button", { name: "Clear" }).slice(-1)[0],
    );
    await waitFor(() => expect(clearCredentials).toHaveBeenCalled());
  });

  it("cancelling the clear confirm dialog does NOT call clearCredentials", async () => {
    getCredentialsStatus.mockResolvedValueOnce({ has_credentials: true });
    render(withQueryClient(<GitHubAppCredentialsCard />));
    await screen.findByText("Credentials are set");

    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument(),
    );
    expect(clearCredentials).not.toHaveBeenCalled();
  });
});
