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

async function isCloudAuthEnabled(): Promise<boolean> {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), STATUS_PROBE_TIMEOUT_MS);
    const res = await fetch(`${INTERNAL_API_URL}/auth/status`, {
      signal: controller.signal,
      cache: "no-store",
    });
    clearTimeout(timer);
    if (!res.ok) return false;
    const data = (await res.json()) as { cloud_auth_enabled?: boolean };
    return data.cloud_auth_enabled === true;
  } catch {
    return false;
  }
}

export async function proxy(request: NextRequest) {
  if (!(await isCloudAuthEnabled())) {
    return NextResponse.next();
  }
  if (!request.cookies.has(SESSION_COOKIE_NAME)) {
    return NextResponse.redirect(new URL("/login", request.url));
  }
  return NextResponse.next();
}

export const config = {
  // Everything except the login page itself (avoids a redirect loop), API
  // routes (nginx routes /api/* straight to the orchestrator in prod — this
  // never sees them there; excluded defensively for a bare `next start`),
  // Next's internal asset paths, and the static icon files at the app root.
  matcher: [
    "/((?!login|api|_next/static|_next/image|favicon.ico|apple-icon.png|icon.png).*)",
  ],
};
