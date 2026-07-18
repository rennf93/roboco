import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

const { waitForTelegramWebApp } = vi.hoisted(() => ({
  waitForTelegramWebApp: vi.fn(),
}));
// Keep the real dev-mock helpers (createDevMockWebApp / isDevMockWebApp) —
// only the bridge resolver is faked.
vi.mock("@/lib/telegram/webapp", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  waitForTelegramWebApp,
}));

const { startTelegramThemeSync } = vi.hoisted(() => ({
  startTelegramThemeSync: vi.fn(() => () => undefined),
}));
vi.mock("@/lib/telegram/theme", () => ({ startTelegramThemeSync }));

const { post } = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("@/lib/api/client", () => ({
  default: { post },
  getErrorMessage: (err: unknown) =>
    (err as { message?: string } | undefined)?.message ?? "Unknown error",
}));

// The cockpit tabs each fetch their own data (queue cards, tasks,
// notifications, A2A) — stubbed out here since this test only exercises the
// bootstrap state machine, not tab content (each tab gets its own coverage).
vi.mock("@/components/tg/tg-tab-bar", () => ({
  TgTabBar: () => <div data-testid="tg-tab-bar" />,
}));
vi.mock("@/components/tg/tg-today-tab", () => ({
  TgTodayTab: () => <div data-testid="tg-today-tab" />,
}));
vi.mock("@/components/tg/tg-approvals-tab", () => ({
  TgApprovalsTab: () => <div data-testid="tg-approvals-tab" />,
}));
vi.mock("@/components/tg/tg-inbox-tab", () => ({
  TgInboxTab: () => <div data-testid="tg-inbox-tab" />,
}));
vi.mock("@/components/tg/tg-board-tab", () => ({
  TgBoardTab: () => <div data-testid="tg-board-tab" />,
}));
vi.mock("@/components/tg/tg-chat-tab", () => ({
  TgChatTab: () => <div data-testid="tg-chat-tab" />,
}));

import TelegramMiniAppPage from "../page";

function mockWebApp(initData = "abc123") {
  return {
    ready: vi.fn(),
    expand: vi.fn(),
    disableVerticalSwipes: vi.fn(),
    initData,
  };
}

describe("TelegramMiniAppPage — auth bootstrap", () => {
  beforeEach(() => {
    waitForTelegramWebApp.mockReset();
    startTelegramThemeSync.mockClear();
    post.mockReset();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("shows a spinner while validating", () => {
    waitForTelegramWebApp.mockReturnValue(new Promise(() => {}));
    render(<TelegramMiniAppPage />);
    expect(screen.getByText(/connecting/i)).toBeInTheDocument();
  });

  it("renders the not-inside-Telegram screen when no WebApp object exists", async () => {
    waitForTelegramWebApp.mockResolvedValue(null);
    render(<TelegramMiniAppPage />);

    await waitFor(() =>
      expect(screen.getByText(/open from telegram/i)).toBeInTheDocument(),
    );
    expect(post).not.toHaveBeenCalled();
  });

  it("calls ready/expand, posts initData, and renders the cockpit on success", async () => {
    const webApp = mockWebApp("real-init-data");
    waitForTelegramWebApp.mockResolvedValue(webApp);
    post.mockResolvedValue({ data: { ok: true } });

    render(<TelegramMiniAppPage />);

    await waitFor(() =>
      expect(screen.getByTestId("tg-tab-bar")).toBeInTheDocument(),
    );
    expect(webApp.ready).toHaveBeenCalledTimes(1);
    expect(webApp.expand).toHaveBeenCalledTimes(1);
    expect(webApp.disableVerticalSwipes).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith("/telegram/webapp-auth", {
      init_data: "real-init-data",
    });
    // Default tab is Today.
    expect(screen.getByTestId("tg-today-tab")).toBeInTheDocument();
  });

  it("renders an error screen with the server's message when auth is refused", async () => {
    waitForTelegramWebApp.mockResolvedValue(mockWebApp());
    post.mockRejectedValue({ message: "Mini App disabled" });

    render(<TelegramMiniAppPage />);

    await waitFor(() =>
      expect(screen.getByText(/couldn.t sign in/i)).toBeInTheDocument(),
    );
    expect(screen.getByText("Mini App disabled")).toBeInTheDocument();
    expect(screen.queryByTestId("tg-tab-bar")).not.toBeInTheDocument();
  });

  it("falls back to the dev mock outside Telegram in development — no auth POST", async () => {
    vi.stubEnv("NODE_ENV", "development");
    waitForTelegramWebApp.mockResolvedValue(null);

    render(<TelegramMiniAppPage />);

    await waitFor(() =>
      expect(screen.getByTestId("tg-tab-bar")).toBeInTheDocument(),
    );
    expect(post).not.toHaveBeenCalled();
    expect(
      screen.queryByText(/open from telegram/i),
    ).not.toBeInTheDocument();
  });

  it("starts Telegram theme sync against the #tg-shell element once ready", async () => {
    const shell = document.createElement("div");
    shell.id = "tg-shell";
    document.body.appendChild(shell);
    try {
      const webApp = mockWebApp();
      waitForTelegramWebApp.mockResolvedValue(webApp);
      post.mockResolvedValue({ data: { ok: true } });

      render(<TelegramMiniAppPage />);

      await waitFor(() =>
        expect(startTelegramThemeSync).toHaveBeenCalledWith(webApp, shell),
      );
    } finally {
      shell.remove();
    }
  });
});
