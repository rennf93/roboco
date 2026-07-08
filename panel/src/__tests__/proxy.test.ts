import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
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

    const res = await proxy(new NextRequest("http://localhost:3000/overview"));
    expect(res.status).toBe(200);
  });

  it("redirects to /login when cloud auth is on and no session cookie", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ cloud_auth_enabled: true }),
    }) as unknown as typeof fetch;
    const { proxy } = await import("../proxy");

    const res = await proxy(new NextRequest("http://localhost:3000/overview"));
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

    const res = await proxy(new NextRequest("http://localhost:3000/overview"));
    expect(res.status).toBe(200);
  });

  it("fails open when the status probe returns a non-ok response", async () => {
    global.fetch = vi
      .fn()
      .mockResolvedValue({ ok: false }) as unknown as typeof fetch;
    const { proxy } = await import("../proxy");

    const res = await proxy(new NextRequest("http://localhost:3000/overview"));
    expect(res.status).toBe(200);
  });
});

describe("isCloudAuthEnabled last-known-good", () => {
  type MockResponse = { ok: boolean; json?: () => Promise<unknown> };

  beforeEach(() => {
    vi.resetModules();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("caches a successful probe and reuses it when the next probe fails", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ cloud_auth_enabled: true }),
      } as MockResponse)
      .mockResolvedValueOnce({ ok: false } as MockResponse);
    global.fetch = fetchMock as unknown as typeof fetch;
    const { isCloudAuthEnabled } = await import("../proxy");
    expect(await isCloudAuthEnabled()).toBe(true);
    // next probe fails — should fall back to cached true, not false
    expect(await isCloudAuthEnabled()).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("fails open to false only when no fresh cache exists", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
    } as MockResponse) as unknown as typeof fetch;
    const { isCloudAuthEnabled } = await import("../proxy");
    expect(await isCloudAuthEnabled()).toBe(false);
  });

  it("treats a cached value older than the TTL as stale", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ cloud_auth_enabled: true }),
      } as MockResponse)
      .mockResolvedValueOnce({ ok: false } as MockResponse);
    global.fetch = fetchMock as unknown as typeof fetch;
    const { isCloudAuthEnabled } = await import("../proxy");
    expect(await isCloudAuthEnabled()).toBe(true);
    vi.advanceTimersByTime(31_000);
    expect(await isCloudAuthEnabled()).toBe(false); // cache expired
  });
});
