import { isAxiosError } from "axios";

// How many times the query client replays a request that never got an HTTP
// response (connection refused or reset, DNS failure, timeout).
export const TRANSPORT_FAILURE_RETRIES = 1;

// True when the request failed before the server answered at all: no HTTP
// response arrived, so replaying it once is the only way to learn anything.
export function isTransportFailure(error: unknown): boolean {
  return isAxiosError(error) && error.response === undefined;
}

// The query client's retry policy: replay only a request the server never
// answered, at most TRANSPORT_FAILURE_RETRIES times. Any HTTP answer is
// final for the query client. A 4xx will not change on replay; a 5xx means
// the server is failing, and replaying it adds load at the worst moment
// (2026-10-28: every failed query was retried once on top of a fan-out of
// ~300 rollup requests, while the DB pool was exhausted). A 429 is already
// replayed after its Retry-After by the API client's own interceptor.
export function shouldRetryQuery(
  failureCount: number,
  error: unknown,
): boolean {
  return failureCount < TRANSPORT_FAILURE_RETRIES && isTransportFailure(error);
}
