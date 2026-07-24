import { describe, it, expect, beforeEach } from "vitest";
import { loadRecents, addRecent } from "@/lib/command-palette-recents";

const STORAGE_KEY = "roboco-cmd-recents";

describe("command-palette-recents", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("returns an empty list when nothing is stored", () => {
    expect(loadRecents()).toEqual([]);
  });

  it("adds an entry to the front and persists it under the expected key", () => {
    const result = addRecent({ type: "task", id: "abc123", title: "Fix bug" });
    expect(result).toEqual([{ type: "task", id: "abc123", title: "Fix bug" }]);
    expect(JSON.parse(window.localStorage.getItem(STORAGE_KEY)!)).toEqual([
      { type: "task", id: "abc123", title: "Fix bug" },
    ]);
  });

  it("moves a re-added entry to the front instead of duplicating it", () => {
    addRecent({ type: "task", id: "a", title: "Task A" });
    addRecent({ type: "task", id: "b", title: "Task B" });
    const result = addRecent({ type: "task", id: "a", title: "Task A (renamed)" });
    expect(result).toEqual([
      { type: "task", id: "a", title: "Task A (renamed)" },
      { type: "task", id: "b", title: "Task B" },
    ]);
  });

  it("caps the list at 10 entries, dropping the oldest", () => {
    for (let i = 0; i < 12; i++) {
      addRecent({ type: "agent", id: `agent-${i}`, title: `Agent ${i}` });
    }
    const result = loadRecents();
    expect(result).toHaveLength(10);
    expect(result[0]).toEqual({ type: "agent", id: "agent-11", title: "Agent 11" });
    expect(result.find((r) => r.id === "agent-0")).toBeUndefined();
  });

  it("ignores malformed JSON and falls back to an empty list", () => {
    window.localStorage.setItem(STORAGE_KEY, "{not json");
    expect(loadRecents()).toEqual([]);
  });

  it("filters out entries missing required shape", () => {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify([{ type: "task", id: "ok", title: "Ok" }, { type: "bogus" }, null]),
    );
    expect(loadRecents()).toEqual([{ type: "task", id: "ok", title: "Ok" }]);
  });
});
