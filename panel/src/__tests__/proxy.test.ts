import { describe, it, expect, vi, afterEach } from "vitest";
import { NextRequest } from "next/server";

describe("proxy", () => {
  const originalFetch = global.fetch;

  afterEach(() => {
    global.fetch = originalFetch;
    vi.resetModules();
  });

  it("passes through when cloud auth is off", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ cloud_auth_enabled: false }),
    }) as unknown as typeof fetch;
    const { proxy } = await import("../proxy");

    const res = await proxy(
      new NextRequest("http://localhost:3000/overview"),
    );
    expect(res.status).toBe(200);
  });

  it("redirects to /login when cloud auth is on and no session cookie", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ cloud_auth_enabled: true }),
    }) as unknown as typeof fetch;
    const { proxy } = await import("../proxy");

    const res = await proxy(
      new NextRequest("http://localhost:3000/overview"),
    );
    expect(res.status).toBe(307);
    expect(res.headers.get("location")).toContain("/login");
  });

  it("passes through when cloud auth is on and a session cookie is present", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ cloud_auth_enabled: true }),
    }) as unknown as typeof fetch;
    const { proxy } = await import("../proxy");

    const req = new NextRequest("http://localhost:3000/overview", {
      headers: { cookie: "roboco_session=abc123" },
    });
    const res = await proxy(req);
    expect(res.status).toBe(200);
  });

  it("fails open (passes through) when the status probe errors", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("network down"));
    const { proxy } = await import("../proxy");

    const res = await proxy(
      new NextRequest("http://localhost:3000/overview"),
    );
    expect(res.status).toBe(200);
  });

  it("fails open when the status probe returns a non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false }) as unknown as
      typeof fetch;
    const { proxy } = await import("../proxy");

    const res = await proxy(
      new NextRequest("http://localhost:3000/overview"),
    );
    expect(res.status).toBe(200);
  });
});
