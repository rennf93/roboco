import { describe, it, expect, vi } from "vitest";

const { redirect } = vi.hoisted(() => ({ redirect: vi.fn() }));
vi.mock("next/navigation", () => ({ redirect }));

import JournalsPage from "../page";

describe("JournalsPage", () => {
  it("redirects to the Agents hub's Journals tab", () => {
    JournalsPage();
    expect(redirect).toHaveBeenCalledWith("/agents?tab=journals");
  });
});
