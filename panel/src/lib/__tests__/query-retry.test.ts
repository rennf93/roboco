import { describe, expect, it } from "vitest";
import { AxiosError, AxiosHeaders } from "axios";
import { shouldRetryQuery } from "../query-retry";

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

function unanswered(): AxiosError {
  return new AxiosError("Network Error", AxiosError.ERR_NETWORK, {
    headers: new AxiosHeaders(),
  });
}

describe("shouldRetryQuery", () => {
  it.each([429, 500, 502, 503, 504, 404, 401, 422])(
    "never replays a request the server answered with %i",
    (status) => {
      expect(shouldRetryQuery(0, answered(status))).toBe(false);
    },
  );

  it("replays a request the server never answered, once", () => {
    expect(shouldRetryQuery(0, unanswered())).toBe(true);
    expect(shouldRetryQuery(1, unanswered())).toBe(false);
  });

  it("does not replay a non-HTTP error (a bug in the query function)", () => {
    expect(shouldRetryQuery(0, new TypeError("x is undefined"))).toBe(false);
  });
});
