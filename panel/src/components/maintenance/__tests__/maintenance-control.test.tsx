import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React, { type ReactNode } from "react";
import type { MaintenancePauseStatus } from "@/lib/api/maintenance";

const { getStatus, pause } = vi.hoisted(() => ({
  getStatus: vi.fn<() => Promise<MaintenancePauseStatus[]>>(),
  pause: vi.fn(),
}));

vi.mock("@/lib/api/maintenance", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/maintenance")>(
    "@/lib/api/maintenance",
  );
  return {
    ...actual,
    maintenanceApi: { list: getStatus, pause, resume: vi.fn() },
  };
});

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

// Real Radix Select needs pointer-capture APIs jsdom doesn't implement, so
// this substitutes the same mock the rest of this codebase uses for any test
// that drives a Select (see projects/settings/__tests__/placement-card.test.tsx).
vi.mock("@/components/ui/select", () => {
  const Ctx = React.createContext<(v: string) => void>(() => {});
  return {
    Select: ({
      value,
      onValueChange,
      children,
    }: {
      value?: string;
      onValueChange?: (v: string) => void;
      children: ReactNode;
    }) => (
      <Ctx.Provider value={onValueChange ?? (() => {})}>
        <div data-testid="duration-select" data-value={value}>
          {children}
        </div>
      </Ctx.Provider>
    ),
    SelectTrigger: ({ children }: { children: ReactNode }) => (
      <div>{children}</div>
    ),
    SelectValue: () => null,
    SelectContent: ({ children }: { children: ReactNode }) => (
      <div>{children}</div>
    ),
    SelectItem: ({
      value,
      children,
    }: {
      value: string;
      children: ReactNode;
    }) => {
      const onValueChange = React.useContext(Ctx);
      return (
        <button type="button" onClick={() => onValueChange(value)}>
          {children}
        </button>
      );
    },
  };
});

import { toast } from "sonner";
import { MaintenanceControl } from "../maintenance-control";

function withQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

function emptyStatus(): MaintenancePauseStatus[] {
  return [];
}

describe("MaintenanceControl, navbar icon button + pause dialog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows a distinct loading tooltip before the first status fetch resolves", async () => {
    getStatus.mockReturnValue(new Promise(() => {})); // never resolves
    const user = userEvent.setup();
    render(withQueryClient(<MaintenanceControl />));

    const button = screen.getByRole("button", {
      name: /operator maintenance pause/i,
    });
    await user.hover(button);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      /checking maintenance-pause status/i,
    );
  });

  it("shows a distinct error tooltip when the status fetch fails, and the dialog still opens with an inline error", async () => {
    getStatus.mockRejectedValue(new Error("network down"));
    const user = userEvent.setup();
    render(withQueryClient(<MaintenanceControl />));

    const button = screen.getByRole("button", {
      name: /operator maintenance pause/i,
    });
    await user.hover(button);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      /status unavailable/i,
    );

    fireEvent.click(button);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /could not be loaded/i,
    );
  });

  it("shows an idle icon and tooltip when no scope is paused", async () => {
    getStatus.mockResolvedValue(emptyStatus());
    const user = userEvent.setup();
    render(withQueryClient(<MaintenanceControl />));

    const button = await screen.findByRole("button", {
      name: /operator maintenance pause/i,
    });
    await user.hover(button);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      /no maintenance pause active/i,
    );
  });

  it("reflects the paused count in the accessible name and tooltip", async () => {
    getStatus.mockResolvedValue([
      {
        scope: "dispatch",
        paused: true,
        paused_by: "ceo",
        paused_at: "2026-08-12T10:00:00Z",
        reason: "NAS migration",
        expires_at: "2026-08-12T14:00:00Z",
      },
    ]);
    render(withQueryClient(<MaintenanceControl />));

    expect(
      await screen.findByRole("button", {
        name: /operator maintenance pause \(1 paused\)/i,
      }),
    ).toBeInTheDocument();
  });

  it("disables Pause until at least one scope is checked, then submits with the picked scope and the default 4-hour duration", async () => {
    getStatus.mockResolvedValue(emptyStatus());
    pause.mockResolvedValue({
      scope: "dispatch",
      paused: true,
      paused_by: "ceo",
      paused_at: "2026-08-12T10:00:00Z",
      reason: null,
      expires_at: "2026-08-12T14:00:00Z",
    });
    const user = userEvent.setup();
    render(withQueryClient(<MaintenanceControl />));

    const button = await screen.findByRole("button", {
      name: /operator maintenance pause/i,
    });
    fireEvent.click(button);

    const confirm = await screen.findByRole("button", {
      name: /pause selected scopes/i,
    });
    expect(confirm).toBeDisabled();

    const checkbox = screen.getByRole("checkbox", {
      name: "Delivery dispatch",
    });
    await user.click(checkbox);

    const confirmWithCount = screen.getByRole("button", {
      name: "Pause 1 scope",
    });
    expect(confirmWithCount).not.toBeDisabled();
    fireEvent.click(confirmWithCount);

    await waitFor(() =>
      expect(pause).toHaveBeenCalledWith("dispatch", {
        reason: undefined,
        hours: 4,
      }),
    );
  });

  it("sends the documented maximum hours when the longest duration option is picked, and trims a whitespace-only reason to undefined", async () => {
    getStatus.mockResolvedValue(emptyStatus());
    pause.mockResolvedValue({
      scope: "board_programs",
      paused: true,
      paused_by: "ceo",
      paused_at: "2026-08-12T10:00:00Z",
      reason: null,
      expires_at: "2026-08-26T10:00:00Z",
    });
    const user = userEvent.setup();
    render(withQueryClient(<MaintenanceControl />));

    fireEvent.click(
      await screen.findByRole("button", {
        name: /operator maintenance pause/i,
      }),
    );

    await user.click(screen.getByRole("checkbox", { name: "Board programs" }));
    await user.type(screen.getByLabelText(/reason/i), "   ");
    fireEvent.click(
      screen.getByRole("button", { name: /14 days \(maximum\)/i }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Pause 1 scope" }));

    await waitFor(() =>
      expect(pause).toHaveBeenCalledWith("board_programs", {
        reason: undefined,
        hours: 336,
      }),
    );
  });

  it("disables the checkbox for an already-paused scope and explains why", async () => {
    getStatus.mockResolvedValue([
      {
        scope: "engines",
        paused: true,
        paused_by: "ceo",
        paused_at: "2026-08-12T10:00:00Z",
        reason: null,
        expires_at: "2026-08-12T14:00:00Z",
      },
    ]);
    render(withQueryClient(<MaintenanceControl />));

    fireEvent.click(
      await screen.findByRole("button", {
        name: /operator maintenance pause \(1 paused\)/i,
      }),
    );

    const checkbox = await screen.findByRole("checkbox", {
      name: "Originating engines",
    });
    expect(checkbox).toBeDisabled();
    expect(screen.getByText(/already paused by ceo/i)).toBeInTheDocument();
  });

  it("closes without pausing when Cancel is clicked", async () => {
    getStatus.mockResolvedValue(emptyStatus());
    const user = userEvent.setup();
    render(withQueryClient(<MaintenanceControl />));

    fireEvent.click(
      await screen.findByRole("button", {
        name: /operator maintenance pause/i,
      }),
    );
    await user.click(
      screen.getByRole("checkbox", { name: "Delivery dispatch" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(pause).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
  });

  it("attempts every selected scope even when one POST fails, and never claims the failed scope paused", async () => {
    getStatus.mockResolvedValue(emptyStatus());
    pause.mockImplementation((scope: string) => {
      if (scope === "dispatch") {
        return Promise.reject(new Error("hours must be > 0"));
      }
      return Promise.resolve({
        scope,
        paused: true,
        paused_by: "ceo",
        paused_at: "2026-08-12T10:00:00Z",
        reason: null,
        expires_at: "2026-08-12T14:00:00Z",
      });
    });
    const user = userEvent.setup();
    render(withQueryClient(<MaintenanceControl />));

    fireEvent.click(
      await screen.findByRole("button", {
        name: /operator maintenance pause/i,
      }),
    );
    await user.click(
      screen.getByRole("checkbox", { name: "Delivery dispatch" }),
    );
    await user.click(screen.getByRole("checkbox", { name: "Board programs" }));
    fireEvent.click(screen.getByRole("button", { name: "Pause 2 scopes" }));

    // Both POSTs were attempted, the failing one didn't stop the other.
    await waitFor(() => expect(pause).toHaveBeenCalledTimes(2));
    expect(pause).toHaveBeenCalledWith("dispatch", {
      reason: undefined,
      hours: 4,
    });
    expect(pause).toHaveBeenCalledWith("board_programs", {
      reason: undefined,
      hours: 4,
    });

    // Partial failure never quietly closes the dialog or claims success.
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await waitFor(() =>
      expect(toast.warning).toHaveBeenCalledWith(
        expect.stringContaining("Paused 1 of 2 scopes"),
      ),
    );
    expect(
      await screen.findByText(/failed to pause: hours must be > 0/i),
    ).toBeInTheDocument();

    // The failed scope stays selected (checked) so it's ready for a retry;
    // the status query is refetched, not locally assumed.
    const dispatchCheckbox = screen.getByRole("checkbox", {
      name: "Delivery dispatch",
    });
    expect(dispatchCheckbox).toBeChecked();
    await waitFor(() => expect(getStatus).toHaveBeenCalledTimes(2));
  });
});
