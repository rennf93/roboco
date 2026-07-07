import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

const { getAll, update } = vi.hoisted(() => ({
  getAll: vi.fn(async () => ({
    notifications_enabled: "false",
    sound_enabled: "false",
    auto_refresh: "false",
    refresh_interval: "45",
  })),
  update: vi.fn(async () => ({})),
}));

vi.mock("@/lib/api", () => ({ settingsApi: { getAll, update } }));

vi.mock("next-themes", () => ({
  useTheme: () => ({ theme: "dark", setTheme: vi.fn() }),
}));

vi.mock("@/store", () => ({
  useUIStore: () => ({ sidebarCollapsed: false, setSidebarCollapsed: vi.fn() }),
}));

vi.mock("@/components/settings/transcript-retention-card", () => ({
  TranscriptRetentionCard: () => null,
}));

vi.mock("@/components/settings/feature-flags-card", () => ({
  FeatureFlagsCard: () => null,
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import SettingsPage from "../page";

function withQueryClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

// The Label and Switch/Select are siblings inside a flex row, so the label
// text doesn't associate with the control. Walk to the row to find it.
function controlFor(labelText: RegExp | string, role: string): HTMLElement {
  const label = screen.getByText(labelText);
  const row = label.closest("div")?.parentElement;
  if (!row) throw new Error(`row not found for ${String(labelText)}`);
  const el = row.querySelector(`[role="${role}"]`);
  if (!el) throw new Error(`${role} not found for ${String(labelText)}`);
  return el as HTMLElement;
}

describe("SettingsPage — Save persists prefs via settingsApi (H16)", () => {
  beforeEach(() => {
    getAll.mockReset();
    update.mockReset();
    getAll.mockResolvedValue({
      notifications_enabled: "false",
      sound_enabled: "false",
      auto_refresh: "false",
      refresh_interval: "45",
    });
    update.mockResolvedValue({});
  });

  it("initializes the prefs from the server, not the hardcoded defaults", async () => {
    render(withQueryClient(<SettingsPage />));

    await waitFor(() =>
      expect(controlFor("Enable Notifications", "switch")).not.toBeChecked(),
    );
    expect(controlFor("Sound Alerts", "switch")).not.toBeChecked();
    expect(controlFor("Auto Refresh", "switch")).not.toBeChecked();
    // refresh_interval "45" overrides the hardcoded "30s" default.
    expect(controlFor("Refresh Interval", "combobox")).not.toHaveTextContent(
      "30s",
    );
  });

  it("persists all four prefs when Save Settings is clicked", async () => {
    render(withQueryClient(<SettingsPage />));

    await waitFor(() =>
      expect(controlFor("Enable Notifications", "switch")).not.toBeChecked(),
    );

    fireEvent.click(screen.getByRole("button", { name: /save settings/i }));

    await waitFor(() => expect(update).toHaveBeenCalledTimes(4));
    expect(update).toHaveBeenCalledWith("notifications_enabled", "false");
    expect(update).toHaveBeenCalledWith("sound_enabled", "false");
    expect(update).toHaveBeenCalledWith("auto_refresh", "false");
    expect(update).toHaveBeenCalledWith("refresh_interval", "45");
  });
});
