import { describe, it, expect, vi } from "vitest";

const { redirect } = vi.hoisted(() => ({ redirect: vi.fn() }));
vi.mock("next/navigation", () => ({ redirect }));

import A2APage from "../page";

describe("A2APage", () => {
  it("redirects to the Agents hub's Conversations tab", () => {
    A2APage();
    expect(redirect).toHaveBeenCalledWith("/agents?tab=conversations");
  });
});
