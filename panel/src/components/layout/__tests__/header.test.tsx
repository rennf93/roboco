import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Header } from "../header";
import { PageRefreshProvider } from "@/components/providers";
import { usePageRefresh } from "@/hooks";
import { useUIStore } from "@/store";

vi.mock("next-themes", () => ({
  useTheme: () => ({ theme: "system", setTheme: vi.fn() }),
}));

// Header now reads the shared ["settings"] query for ceo_name; stub it so
// tests don't hit the network. Individual tests can override the resolved
// value via mockResolvedValueOnce.
const { getAll } = vi.hoisted(() => ({
  getAll: vi.fn(async () => ({}) as Record<string, string>),
}));
vi.mock("@/lib/api", () => ({ settingsApi: { getAll } }));

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
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return (
    <QueryClientProvider client={client}>
      <PageRefreshProvider>{ui}</PageRefreshProvider>
    </QueryClientProvider>
  );
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

describe("Header — command palette trigger", () => {
  beforeEach(() => {
    useUIStore.setState({ commandPaletteOpen: false });
  });

  it("has no disabled search input or 'Coming Soon' remnant", () => {
    const { container } = render(withPageRefresh(<Header />));

    expect(container.querySelector('input[type="search"]')).toBeNull();
    expect(screen.queryByText("Coming Soon")).not.toBeInTheDocument();
  });

  it("opens the command palette when the search trigger is clicked", () => {
    render(withPageRefresh(<Header />));

    const trigger = screen.getByRole("button", {
      name: /search tasks, agents/i,
    });
    trigger.click();

    expect(useUIStore.getState().commandPaletteOpen).toBe(true);
  });
});

describe("Header — CEO name chip (ceo_name setting)", () => {
  beforeEach(() => {
    getAll.mockClear();
  });

  it("falls back to the default name while the settings query is unset", async () => {
    getAll.mockResolvedValueOnce({});
    render(withPageRefresh(<Header />));
    expect(await screen.findByText("Renzo")).toBeInTheDocument();
  });

  it("renders the persisted ceo_name once the settings query resolves", async () => {
    getAll.mockResolvedValueOnce({ ceo_name: "Alice" });
    render(withPageRefresh(<Header />));
    expect(await screen.findByText("Alice")).toBeInTheDocument();
    expect(screen.queryByText("Renzo")).not.toBeInTheDocument();
  });
});
