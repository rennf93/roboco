import { describe, it, expect } from "vitest";
import { buildRouteKey } from "../scroll-restoration";

describe("buildRouteKey", () => {
  it("drops UI-only params so they don't fork the saved scroll position", () => {
    const withExpanded = new URLSearchParams("status=open&expanded=abc,def");
    const withoutExpanded = new URLSearchParams("status=open");

    expect(buildRouteKey("/tasks", withExpanded)).toBe(
      buildRouteKey("/tasks", withoutExpanded),
    );
  });

  it("keeps real navigation params", () => {
    expect(buildRouteKey("/tasks", new URLSearchParams("status=open"))).toBe(
      "/tasks?status=open",
    );
  });
});
