import { describe, it, expect, vi } from "vitest";
import { applyTelegramTheme, startTelegramThemeSync } from "../theme";
import type { TelegramWebApp } from "../webapp";

function webAppWith(overrides: Partial<TelegramWebApp>): TelegramWebApp {
  return {
    ready: () => undefined,
    expand: () => undefined,
    initData: "",
    ...overrides,
  };
}

describe("applyTelegramTheme", () => {
  it("toggles the dark class from colorScheme", () => {
    const el = document.createElement("div");
    applyTelegramTheme(webAppWith({ colorScheme: "dark" }), el);
    expect(el.classList.contains("dark")).toBe(true);
    applyTelegramTheme(webAppWith({ colorScheme: "light" }), el);
    expect(el.classList.contains("dark")).toBe(false);
  });

  it("maps surface themeParams onto the panel CSS variables — never the accent", () => {
    const el = document.createElement("div");
    applyTelegramTheme(
      webAppWith({
        themeParams: {
          bg_color: "#17212b",
          text_color: "#f5f5f5",
          hint_color: "#708499",
          button_color: "#5288c1",
          button_text_color: "#ffffff",
        },
      }),
      el,
    );
    expect(el.style.getPropertyValue("--background")).toBe("#17212b");
    expect(el.style.getPropertyValue("--card")).toBe("#17212b");
    expect(el.style.getPropertyValue("--foreground")).toBe("#f5f5f5");
    expect(el.style.getPropertyValue("--muted-foreground")).toBe("#708499");
    // The accent stays RoboCo's (#tg-shell skin) — button colors are
    // deliberately not adopted.
    expect(el.style.getPropertyValue("--primary")).toBe("");
    expect(el.style.getPropertyValue("--primary-foreground")).toBe("");
  });

  it("prefers the more specific section/secondary keys when present", () => {
    const el = document.createElement("div");
    applyTelegramTheme(
      webAppWith({
        themeParams: {
          bg_color: "#111111",
          secondary_bg_color: "#222222",
          section_bg_color: "#333333",
        },
      }),
      el,
    );
    expect(el.style.getPropertyValue("--background")).toBe("#222222");
    expect(el.style.getPropertyValue("--card")).toBe("#333333");
  });

  it("drops non-hex values instead of injecting them into style", () => {
    const el = document.createElement("div");
    applyTelegramTheme(
      webAppWith({
        themeParams: {
          bg_color: "url(javascript:alert(1))",
          text_color: "#abc",
        },
      }),
      el,
    );
    expect(el.style.getPropertyValue("--background")).toBe("");
    expect(el.style.getPropertyValue("--foreground")).toBe("");
  });
});

describe("startTelegramThemeSync", () => {
  it("applies immediately, re-applies on themeChanged, and unsubscribes on cleanup", () => {
    const el = document.createElement("div");
    const listeners = new Map<string, () => void>();
    const webApp = webAppWith({
      colorScheme: "light",
      themeParams: { bg_color: "#ffffff" },
      onEvent: vi.fn((event: string, cb: () => void) => {
        listeners.set(event, cb);
      }),
      offEvent: vi.fn((event: string) => {
        listeners.delete(event);
      }),
    });

    const stop = startTelegramThemeSync(webApp, el);
    expect(el.style.getPropertyValue("--background")).toBe("#ffffff");

    webApp.colorScheme = "dark";
    webApp.themeParams = { bg_color: "#17212b" };
    listeners.get("themeChanged")?.();
    expect(el.style.getPropertyValue("--background")).toBe("#17212b");
    expect(el.classList.contains("dark")).toBe(true);

    stop();
    expect(webApp.offEvent).toHaveBeenCalledTimes(1);
    expect(listeners.has("themeChanged")).toBe(false);
  });

  it("is a one-shot apply with no-op cleanup when the bridge lacks events", () => {
    const el = document.createElement("div");
    const stop = startTelegramThemeSync(
      webAppWith({ themeParams: { bg_color: "#123456" } }),
      el,
    );
    expect(el.style.getPropertyValue("--background")).toBe("#123456");
    expect(stop).not.toThrow();
  });
});
