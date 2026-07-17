import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// Server-side only (no NEXT_PUBLIC_ prefix — never reaches the browser
// bundle). Runs inside the panel container and reaches the orchestrator over
// the docker-internal network (roboco_default), not through nginx — a request
// back out through the panel's own public origin would be a container calling
// itself over the internet. Defaults to the compose service name.
const INTERNAL_API_URL =
  process.env.INTERNAL_API_URL || "http://roboco-orchestrator:8000/api";

// Must match roboco.api.auth.backend.SESSION_COOKIE_NAME.
const SESSION_COOKIE_NAME = "roboco_session";

// The probe must never block navigation: a slow/unreachable backend fails
// open to "cloud auth off" (the safe default — off is what every deploy
// starts on), not a stuck redirect.
const STATUS_PROBE_TIMEOUT_MS = 1500;

const PROBE_TTL_MS = 30_000;
let lastKnown: { value: boolean; at: number } | null = null;

export async function isCloudAuthEnabled(): Promise<boolean> {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), STATUS_PROBE_TIMEOUT_MS);
    const res = await fetch(`${INTERNAL_API_URL}/auth/status`, {
      signal: controller.signal,
      cache: "no-store",
    });
    clearTimeout(timer);
    if (res.ok) {
      const data = (await res.json()) as { cloud_auth_enabled?: boolean };
      const value = data.cloud_auth_enabled === true;
      lastKnown = { value, at: Date.now() };
      return value;
    }
    // Non-ok response: fall back to a fresh cache rather than fail open.
  } catch {
    // Network/timeout: fall back to a fresh cache rather than fail open.
  }
  if (lastKnown && Date.now() - lastKnown.at < PROBE_TTL_MS) {
    return lastKnown.value;
  }
  // No fresh cache: the safe default is "off" (what every deploy starts on).
  return false;
}

export async function proxy(request: NextRequest) {
  if (!(await isCloudAuthEnabled())) {
    return NextResponse.next();
  }
  // UX redirect only — shields dashboard chrome from flashing before login. The API (/api/*) enforces auth independently; a stale cookie shows chrome then 401s on the first API call.
  if (!request.cookies.has(SESSION_COOKIE_NAME)) {
    return NextResponse.redirect(new URL("/login", request.url));
  }
  return NextResponse.next();
}

export const config = {
  // Everything except the login page itself (avoids a redirect loop), API
  // routes (nginx routes /api/* straight to the orchestrator in prod — this
  // never sees them there; excluded defensively for a bare `next start`),
  // the Telegram Mini App surface (/tg authenticates via Telegram initData,
  // not the password-login cookie — redirecting it to /login would strand a
  // phone session that can never reach that page), Next's internal asset
  // paths, and the static icon files at the app root.
  matcher: [
    "/((?!login|api|tg|_next/static|_next/image|favicon.ico|apple-icon.png|icon.png).*)",
  ],
};
