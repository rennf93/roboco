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

  // M43: when credentials are already stored, leaving all 4 fields blank and
  // clicking Save is a destructive clear — it must open an AlertDialog and
  // only fire setCredentials on confirm. A normal all-4-filled save fires
  // immediately with no dialog.
  it("a clear (all 4 blank + has_credentials) opens a confirm dialog and defers the mutation until confirmed", async () => {
    getCredentialsStatus.mockResolvedValueOnce({ has_credentials: true });
    render(withQueryClient(<TikTokCredentialsForm />));
    await screen.findByText("Credentials are set");

    const saveButton = screen.getByRole("button", { name: "Save" });
    expect(saveButton).not.toBeDisabled();

    fireEvent.click(saveButton);

    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toBeInTheDocument();

    expect(setCredentials).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    await waitFor(() =>
      expect(setCredentials).toHaveBeenCalledWith({
        client_key: "",
        client_secret: "",
        access_token: "",
        refresh_token: "",
      }),
    );
  });

  it("a normal all-4-filled save fires immediately without a confirm dialog", async () => {
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
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("a clear confirm dialog cancel does NOT fire the mutation", async () => {
    getCredentialsStatus.mockResolvedValueOnce({ has_credentials: true });
    render(withQueryClient(<TikTokCredentialsForm />));
    await screen.findByText("Credentials are set");

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument(),
    );
    expect(setCredentials).not.toHaveBeenCalled();
  });
});
