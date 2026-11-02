import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { AxiosError, AxiosHeaders } from "axios";

vi.mock("@/hooks/use-agents", () => ({ useAgentRosterSync: vi.fn() }));

import { Providers } from "../app-providers";

function answered(status: number): AxiosError {
  const config = { headers: new AxiosHeaders() };
  return new AxiosError("request failed", undefined, config, undefined, {
    status,
    statusText: String(status),
    headers: {},
    config,
    data: {},
  });
}

function appQueryClient(): QueryClient {
  let client: QueryClient | undefined;
  function Probe() {
    client = useQueryClient();
    return null;
  }
  render(
    <Providers>
      <Probe />
    </Providers>,
  );
  if (!client) throw new Error("Providers rendered no QueryClient");
  return client;
}

describe("Providers query client", () => {
  it("never replays a query the server answered with 429 or 5xx", () => {
    // 2026-10-28: `retry: 1` replayed every failed rollup query once while
    // the orchestrator's DB pool was already exhausted.
    const retry = appQueryClient().getDefaultOptions().queries?.retry;
    expect(typeof retry).toBe("function");
    const shouldRetry = retry as (count: number, error: unknown) => boolean;
    for (const status of [429, 500, 503]) {
      expect(shouldRetry(0, answered(status))).toBe(false);
    }
  });
});
