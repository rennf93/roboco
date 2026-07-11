import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render } from "@testing-library/react";
import { toast } from "sonner";
import { NotificationAlerts } from "../notification-alerts";
import type { NotificationMessage } from "@/hooks/use-websocket";

// Mutable stand-in for the WS notification stream — reassign (never mutate in
// place) `list` between renders so the effect's dependency actually changes.
const mockStream = vi.hoisted(() => ({
  list: [] as NotificationMessage[],
}));

vi.mock("@/hooks/use-websocket", () => ({
  useNotificationStream: () => ({ notifications: mockStream.list }),
}));

// Non-reactive stand-in for the persisted UI store (zustand selector form).
const mockUiStore = vi.hoisted(() => ({
  notificationsEnabled: true,
  soundEnabled: true,
}));

vi.mock("@/store", () => ({
  useUIStore: (selector: (s: typeof mockUiStore) => unknown) =>
    selector(mockUiStore),
}));

vi.mock("sonner", () => ({ toast: vi.fn() }));

function notification(overrides: Partial<NotificationMessage>) {
  return { type: "notification", ...overrides } as NotificationMessage;
}

// AudioContext doesn't exist in jsdom; stub a constructible fake so playChime
// can run its real path instead of hitting the "no AudioContext" early return.
function stubAudioContext() {
  const ctor = vi.fn(function FakeAudioContext() {
    return {
      currentTime: 0,
      createOscillator: () => ({
        type: "sine",
        frequency: { value: 0 },
        connect: (dest: unknown) => dest,
        start: vi.fn(),
        stop: vi.fn(),
        onended: null,
      }),
      createGain: () => ({
        gain: { value: 0 },
        connect: (dest: unknown) => dest,
      }),
      close: vi.fn(),
    };
  });
  vi.stubGlobal("AudioContext", ctor);
  return ctor;
}

describe("NotificationAlerts", () => {
  beforeEach(() => {
    mockStream.list = [];
    mockUiStore.notificationsEnabled = true;
    mockUiStore.soundEnabled = true;
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("ignores whatever the stream already has on mount (no backlog toast)", () => {
    mockStream.list = [
      notification({ notification_id: "1", subject: "Old one" }),
    ];
    render(<NotificationAlerts />);
    expect(toast).not.toHaveBeenCalled();
  });

  it("toasts a newly-arrived notification while enabled", () => {
    const { rerender } = render(<NotificationAlerts />);
    expect(toast).not.toHaveBeenCalled();

    mockStream.list = [
      ...mockStream.list,
      notification({ notification_id: "2", subject: "New task", priority: "high" }),
    ];
    rerender(<NotificationAlerts />);

    expect(toast).toHaveBeenCalledTimes(1);
    expect(toast).toHaveBeenCalledWith(
      "New task",
      expect.objectContaining({ description: expect.stringContaining("high") }),
    );
  });

  it("does not toast when notifications are disabled", () => {
    mockUiStore.notificationsEnabled = false;
    const { rerender } = render(<NotificationAlerts />);

    mockStream.list = [
      ...mockStream.list,
      notification({ notification_id: "3", subject: "Silenced" }),
    ];
    rerender(<NotificationAlerts />);

    expect(toast).not.toHaveBeenCalled();
  });

  it("plays a chime via Web Audio when sound is enabled", () => {
    const ctor = stubAudioContext();
    const { rerender } = render(<NotificationAlerts />);

    mockStream.list = [
      ...mockStream.list,
      notification({ notification_id: "4", subject: "Ping" }),
    ];
    rerender(<NotificationAlerts />);

    expect(ctor).toHaveBeenCalledTimes(1);
  });

  it("skips the chime when sound is disabled, but still toasts", () => {
    const ctor = stubAudioContext();
    mockUiStore.soundEnabled = false;
    const { rerender } = render(<NotificationAlerts />);

    mockStream.list = [
      ...mockStream.list,
      notification({ notification_id: "5", subject: "Quiet" }),
    ];
    rerender(<NotificationAlerts />);

    expect(toast).toHaveBeenCalledTimes(1);
    expect(ctor).not.toHaveBeenCalled();
  });

  it("never crashes when AudioContext is unavailable (autoplay-blocked browsers)", () => {
    vi.stubGlobal("AudioContext", undefined);
    const { rerender } = render(<NotificationAlerts />);

    mockStream.list = [
      ...mockStream.list,
      notification({ notification_id: "6", subject: "Still fine" }),
    ];
    expect(() => rerender(<NotificationAlerts />)).not.toThrow();
    expect(toast).toHaveBeenCalledTimes(1);
  });
});
