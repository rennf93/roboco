import { describe, it, expect } from "vitest";
import {
  QUICK_ACTIONS_REGISTRY,
  DEFAULT_QUICK_ACTION_IDS,
  resolveQuickActions,
  isKnownQuickActionId,
} from "../quick-actions-registry";

describe("quick-actions-registry", () => {
  it("has unique ids", () => {
    const ids = QUICK_ACTIONS_REGISTRY.map((a) => a.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("every action has a non-empty, absolute href", () => {
    for (const action of QUICK_ACTIONS_REGISTRY) {
      expect(action.href).toBeTruthy();
      expect(action.href.startsWith("/")).toBe(true);
    }
  });

  it("every action has a non-empty label, tip, and icon", () => {
    for (const action of QUICK_ACTIONS_REGISTRY) {
      expect(action.label).toBeTruthy();
      expect(action.tip).toBeTruthy();
      expect(action.icon).toBeTruthy();
    }
  });

  it("default ids all resolve to real registry entries", () => {
    for (const id of DEFAULT_QUICK_ACTION_IDS) {
      expect(isKnownQuickActionId(id)).toBe(true);
    }
  });

  it("resolveQuickActions preserves order and drops unknown ids", () => {
    const resolved = resolveQuickActions([
      "settings",
      "does-not-exist",
      "tasks",
    ]);
    expect(resolved.map((a) => a.id)).toEqual(["settings", "tasks"]);
  });

  it("resolveQuickActions returns an empty list for an all-stale input", () => {
    expect(resolveQuickActions(["ghost-1", "ghost-2"])).toEqual([]);
  });
});
