import { describe, expect, it } from "vitest";
import { useUIStore } from "../ui-store";
import { DEFAULT_QUICK_ACTION_IDS } from "@/components/dashboard/quick-actions-registry";

describe("ui-store persistence contract", () => {
  it("persists quickActionIds through partialize (a key missing here silently stops persisting)", () => {
    const partialize = useUIStore.persist.getOptions().partialize;
    expect(partialize).toBeDefined();
    const persisted = partialize!(useUIStore.getState()) as Record<string, unknown>;
    expect(persisted.quickActionIds).toEqual(DEFAULT_QUICK_ACTION_IDS);
  });

  it("reset restores the exact default list after customization", () => {
    useUIStore.getState().setQuickActionIds(["tasks"]);
    expect(useUIStore.getState().quickActionIds).toEqual(["tasks"]);
    useUIStore.getState().resetQuickActionIds();
    expect(useUIStore.getState().quickActionIds).toEqual(DEFAULT_QUICK_ACTION_IDS);
  });
});
