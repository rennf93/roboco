import { describe, it, expect, vi } from "vitest";

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock("../client", () => ({ default: { get, post } }));

import { playbooksApi } from "../playbooks";

describe("playbooksApi", () => {
  it("listApproved requests status=approved", async () => {
    get.mockResolvedValueOnce({ data: [{ id: "a" }] });
    await expect(playbooksApi.listApproved()).resolves.toEqual([{ id: "a" }]);
    expect(get).toHaveBeenCalledWith("/playbooks", {
      params: { status: "approved" },
    });
  });

  it("archive posts to the archive route", async () => {
    post.mockResolvedValueOnce({ data: { id: "a", status: "archived" } });
    await expect(playbooksApi.archive("a")).resolves.toEqual({
      id: "a",
      status: "archived",
    });
    expect(post).toHaveBeenCalledWith("/playbooks/a/archive");
  });
});
