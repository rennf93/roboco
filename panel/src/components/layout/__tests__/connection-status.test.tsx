import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConnectionStatus } from "../connection-status";

// The badge went icon-only (CEO request): "Checking...", "Connected", and
// "Offline" no longer render as visible text in any state, the tooltip is
// now the only word-based reading, and an aria-label carries the accessible
// name for the icon-only control.
describe("ConnectionStatus, icon-only badge with accessible name", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("renders no visible text once connected, exposing the state only via aria-label", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true } as Response),
    );

    render(<ConnectionStatus />);

    const status = await screen.findByRole("status", {
      name: /orchestrator api reachable/i,
    });
    expect(status).toBeInTheDocument();
    expect(screen.queryByText("Connected")).not.toBeInTheDocument();
    expect(screen.queryByText(/checking/i)).not.toBeInTheDocument();
  });

  it("renders no visible text when offline, exposing the state only via aria-label", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("network down")),
    );

    render(<ConnectionStatus />);

    const status = await screen.findByRole("status", {
      name: /orchestrator api unreachable/i,
    });
    expect(status).toBeInTheDocument();
    expect(screen.queryByText("Offline")).not.toBeInTheDocument();
  });

  it("shows an accessible checking state before the first fetch resolves, with no visible label", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise(() => {})), // never resolves within this test
    );

    render(<ConnectionStatus />);

    const status = screen.getByRole("status", { name: /checking/i });
    expect(status).toBeInTheDocument();
    expect(screen.queryByText("Checking...")).not.toBeInTheDocument();
  });

  it("keeps the full state explanation available via the hover tooltip", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true } as Response),
    );
    const user = userEvent.setup();

    render(<ConnectionStatus />);

    const status = await screen.findByRole("status", {
      name: /orchestrator api reachable/i,
    });
    await user.hover(status);

    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      /orchestrator api reachable.*re-checked every 30s/i,
    );
  });

  it("keeps the disconnected tooltip fully explanatory, not just a short label", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("network down")),
    );
    const user = userEvent.setup();

    render(<ConnectionStatus />);

    const status = await screen.findByRole("status", {
      name: /orchestrator api unreachable/i,
    });
    await user.hover(status);

    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      /orchestrator api unreachable.*retrying every 30s/i,
    );
  });
});
