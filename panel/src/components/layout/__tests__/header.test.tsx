import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { useState, type ReactNode } from "react";
import { Header } from "../header";
import { PageRefreshProvider } from "@/components/providers";
import { usePageRefresh } from "@/hooks";

vi.mock("next-themes", () => ({
  useTheme: () => ({ theme: "system", setTheme: vi.fn() }),
}));

vi.mock("@/hooks/use-websocket", () => ({
  useNotificationStream: () => ({
    notifications: [],
    isConnected: true,
    clearMessages: vi.fn(),
  }),
}));

vi.mock("@/components/layout/connection-status", () => ({
  ConnectionStatus: () => <div data-testid="connection-status" />,
}));

vi.mock("@/components/layout/mobile-sidebar", () => ({
  MobileSidebar: () => <div data-testid="mobile-sidebar" />,
}));

vi.mock("@/components/notifications/notification-bell", () => ({
  NotificationBell: () => <div data-testid="notification-bell" />,
}));

function withPageRefresh(ui: ReactNode) {
  return <PageRefreshProvider>{ui}</PageRefreshProvider>;
}

function RefreshRegistrator({
  callback,
}: {
  callback: () => void | Promise<void>;
}) {
  const { register, unregister } = usePageRefresh();
  const [registered, setRegistered] = useState(false);

  if (!registered) {
    register(callback);
    setRegistered(true);
  }

  return (
    <button
      type="button"
      onClick={() => unregister(callback)}
      data-testid="unregister"
    >
      Unregister
    </button>
  );
}

describe("Header — navbar refresh button", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the refresh button between the connection status and theme toggle", () => {
    render(withPageRefresh(<Header />));

    const refreshButton = screen.getByRole("button", {
      name: /refresh only the current page/i,
    });
    expect(refreshButton).toBeInTheDocument();

    const connectionStatus = screen.getByTestId("connection-status");
    const themeButton = screen.getByRole("button", { name: /toggle theme/i });

    expect(connectionStatus.compareDocumentPosition(refreshButton)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    expect(refreshButton.compareDocumentPosition(themeButton)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });

  it("exposes an accessible tooltip/label that clarifies the refresh is page-scoped", () => {
    render(withPageRefresh(<Header />));

    const refreshButton = screen.getByRole("button", {
      name: /refresh only the current page/i,
    });
    expect(refreshButton).toHaveAttribute(
      "aria-label",
      "Refresh only the current page",
    );
    // The hover hint is a Radix tooltip now (no native title attribute).
    expect(refreshButton).not.toHaveAttribute("title");
  });

  it("disables the refresh button when no page has registered a refresh callback", () => {
    render(withPageRefresh(<Header />));

    const refreshButton = screen.getByRole("button", {
      name: /refresh only the current page/i,
    });

    expect(refreshButton).toBeDisabled();
  });

  it("shows a spinner while the registered refresh callback is running and disables the button", async () => {
    let resolveRefresh: (() => void) | undefined;
    const deferred = new Promise<void>((resolve) => {
      resolveRefresh = resolve;
    });
    const callback = vi.fn(() => deferred);

    render(
      withPageRefresh(
        <>
          <RefreshRegistrator callback={callback} />
          <Header />
        </>,
      ),
    );

    const refreshButton = screen.getByRole("button", {
      name: /refresh only the current page/i,
    });

    refreshButton.click();

    await waitFor(() => expect(refreshButton).toBeDisabled());
    expect(callback).toHaveBeenCalledTimes(1);

    resolveRefresh?.();
    await waitFor(() => expect(refreshButton).not.toBeDisabled());
  });

  it("is not clickable again until the previous refresh finishes", async () => {
    let resolveRefresh: (() => void) | undefined;
    const deferred = new Promise<void>((resolve) => {
      resolveRefresh = resolve;
    });
    const callback = vi.fn(() => deferred);

    render(
      withPageRefresh(
        <>
          <RefreshRegistrator callback={callback} />
          <Header />
        </>,
      ),
    );

    const refreshButton = screen.getByRole("button", {
      name: /refresh only the current page/i,
    });

    refreshButton.click();
    refreshButton.click();

    await waitFor(() => expect(callback).toHaveBeenCalledTimes(1));

    resolveRefresh?.();
    await waitFor(() => expect(refreshButton).not.toBeDisabled());
  });
});
