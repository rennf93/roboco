import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

// The four prefs below are CLIENT-ONLY (never sent to the backend — the
// server's settings allowlist is transcript_retention_days + feature flags
// only, see roboco/services/settings.py). This mock stands in for the
// persisted UI store; mutate its fields per-test to control what the page
// renders.
const mockStore = vi.hoisted(() => ({
  sidebarCollapsed: false,
  setSidebarCollapsed: vi.fn(),
  notificationsEnabled: true,
  setNotificationsEnabled: vi.fn(),
  soundEnabled: true,
  setSoundEnabled: vi.fn(),
  autoRefresh: false,
  setAutoRefresh: vi.fn(),
  refreshIntervalSeconds: 30,
  setRefreshIntervalSeconds: vi.fn(),
}));

vi.mock("@/store", () => ({ useUIStore: () => mockStore }));

vi.mock("next-themes", () => ({
  useTheme: () => ({ theme: "dark", setTheme: vi.fn() }),
}));

vi.mock("@/components/settings/transcript-retention-card", () => ({
  TranscriptRetentionCard: () => null,
}));

vi.mock("@/components/settings/feature-flags-card", () => ({
  FeatureFlagsCard: () => null,
}));

vi.mock("@/components/settings/github-app-credentials-card", () => ({
  GitHubAppCredentialsCard: () => null,
}));

import SettingsPage from "../page";

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

function resetStore() {
  mockStore.sidebarCollapsed = false;
  mockStore.notificationsEnabled = true;
  mockStore.soundEnabled = true;
  mockStore.autoRefresh = false;
  mockStore.refreshIntervalSeconds = 30;
  for (const fn of [
    mockStore.setSidebarCollapsed,
    mockStore.setNotificationsEnabled,
    mockStore.setSoundEnabled,
    mockStore.setAutoRefresh,
    mockStore.setRefreshIntervalSeconds,
  ]) {
    fn.mockReset();
  }
}

describe("SettingsPage — client-only prefs (store-driven, no server round trip)", () => {
  beforeEach(() => {
    resetStore();
  });

  it("has no Save Settings button — every pref is instant-apply", () => {
    render(<SettingsPage />);
    expect(
      screen.queryByRole("button", { name: /save settings/i }),
    ).not.toBeInTheDocument();
  });

  it("renders the four prefs from the store", () => {
    mockStore.notificationsEnabled = false;
    mockStore.soundEnabled = false;
    mockStore.autoRefresh = true;
    mockStore.refreshIntervalSeconds = 60;
    render(<SettingsPage />);

    expect(controlFor("Enable Notifications", "switch")).not.toBeChecked();
    expect(controlFor("Sound Alerts", "switch")).not.toBeChecked();
    expect(controlFor("Auto Refresh", "switch")).toBeChecked();
    expect(controlFor("Refresh Interval", "combobox")).toHaveTextContent(
      "1m",
    );
  });

  it("toggling Auto Refresh calls setAutoRefresh directly — no edits/save step", () => {
    render(<SettingsPage />);
    fireEvent.click(controlFor("Auto Refresh", "switch"));
    expect(mockStore.setAutoRefresh).toHaveBeenCalledWith(true);
  });

  it("toggling Enable Notifications calls setNotificationsEnabled directly", () => {
    render(<SettingsPage />);
    fireEvent.click(controlFor("Enable Notifications", "switch"));
    expect(mockStore.setNotificationsEnabled).toHaveBeenCalledWith(false);
  });

  it("Refresh Interval select is disabled while Auto Refresh is off", () => {
    render(<SettingsPage />);
    expect(controlFor("Refresh Interval", "combobox")).toBeDisabled();
  });

  it("Sound Alerts switch stays disabled — and inert — when notifications are off", () => {
    mockStore.notificationsEnabled = false;
    render(<SettingsPage />);
    const soundSwitch = controlFor("Sound Alerts", "switch");
    expect(soundSwitch).toBeDisabled();

    fireEvent.click(soundSwitch);
    expect(mockStore.setSoundEnabled).not.toHaveBeenCalled();
  });

  // W9-5 follow-up: the disabled Refresh Interval / Sound Alerts controls
  // now carry a tooltip on their label explaining why — but only while
  // actually disabled. TooltipTrigger always stamps data-state onto its
  // asChild target, so its presence/absence proxies "is this label
  // tooltip-wrapped" without simulating hover.
  it("Refresh Interval and Sound Alerts labels carry a disabled-reason tooltip only while disabled", () => {
    const { rerender } = render(<SettingsPage />); // autoRefresh: false, notificationsEnabled: true (reset default)
    expect(
      screen.getByText("Refresh Interval").getAttribute("data-state"),
    ).toBe("closed");
    expect(screen.getByText("Sound Alerts").getAttribute("data-state")).toBe(
      null,
    );

    mockStore.autoRefresh = true;
    mockStore.notificationsEnabled = false;
    rerender(<SettingsPage />);
    expect(
      screen.getByText("Refresh Interval").getAttribute("data-state"),
    ).toBe(null);
    expect(screen.getByText("Sound Alerts").getAttribute("data-state")).toBe(
      "closed",
    );
  });
});
