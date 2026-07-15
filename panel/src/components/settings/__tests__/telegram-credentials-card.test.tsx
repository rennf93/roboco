import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

const { getCredentialsStatus, setCredentials } = vi.hoisted(() => ({
  getCredentialsStatus: vi.fn(async () => ({ has_credentials: false })),
  setCredentials: vi.fn(async () => ({ has_credentials: true })),
}));

vi.mock("@/lib/api", () => ({
  telegramApi: { getCredentialsStatus, setCredentials },
}));

import { TelegramCredentialsForm } from "../telegram-credentials-card";

function withQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

describe("TelegramCredentialsForm", () => {
  beforeEach(() => {
    getCredentialsStatus.mockClear();
    setCredentials.mockClear();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows 'no credentials configured' by default", async () => {
    render(withQueryClient(<TelegramCredentialsForm />));
    expect(
      await screen.findByText("No credentials configured"),
    ).toBeInTheDocument();
  });

  it("disables Save until both fields are filled", async () => {
    render(withQueryClient(<TelegramCredentialsForm />));
    await screen.findByText("No credentials configured");
    const saveButton = screen.getByRole("button", { name: "Save" });
    expect(saveButton).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Bot token (from @BotFather)"), {
      target: { value: "123:abc" },
    });
    expect(saveButton).toBeDisabled(); // chat id still unfilled

    fireEvent.change(screen.getByLabelText("Chat id (destination)"), {
      target: { value: "987" },
    });
    expect(saveButton).not.toBeDisabled();
  });

  it("saves both secrets and clears the inputs on success", async () => {
    render(withQueryClient(<TelegramCredentialsForm />));
    await screen.findByText("No credentials configured");

    fireEvent.change(screen.getByLabelText("Bot token (from @BotFather)"), {
      target: { value: "123:abc" },
    });
    fireEvent.change(screen.getByLabelText("Chat id (destination)"), {
      target: { value: "987" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(setCredentials).toHaveBeenCalledWith({
        bot_token: "123:abc",
        chat_id: "987",
      }),
    );
    await waitFor(() =>
      expect(
        (
          screen.getByLabelText("Bot token (from @BotFather)") as HTMLInputElement
        ).value,
      ).toBe(""),
    );
  });

  it("a clear (both blank + has_credentials) opens a confirm dialog and defers the mutation until confirmed", async () => {
    getCredentialsStatus.mockResolvedValueOnce({ has_credentials: true });
    render(withQueryClient(<TelegramCredentialsForm />));
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
        bot_token: "",
        chat_id: "",
      }),
    );
  });

  it("a normal both-filled save fires immediately without a confirm dialog", async () => {
    render(withQueryClient(<TelegramCredentialsForm />));
    await screen.findByText("No credentials configured");

    fireEvent.change(screen.getByLabelText("Bot token (from @BotFather)"), {
      target: { value: "123:abc" },
    });
    fireEvent.change(screen.getByLabelText("Chat id (destination)"), {
      target: { value: "987" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(setCredentials).toHaveBeenCalledWith({
        bot_token: "123:abc",
        chat_id: "987",
      }),
    );
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("a clear confirm dialog cancel does NOT fire the mutation", async () => {
    getCredentialsStatus.mockResolvedValueOnce({ has_credentials: true });
    render(withQueryClient(<TelegramCredentialsForm />));
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