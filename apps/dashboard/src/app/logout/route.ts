import { type NextRequest, NextResponse } from "next/server";

import { SESSION_COOKIE } from "@/lib/session";

/**
 * Clears a session the API no longer accepts (expired or revoked key).
 * Server Components cannot modify cookies, so pages redirect here instead.
 * Only ever signs the visitor out, so it is safe as a GET.
 */
export function GET(request: NextRequest) {
  const login = new URL("/login", request.url);
  login.searchParams.set("reason", "expired");
  const next = request.nextUrl.searchParams.get("next");
  if (next?.startsWith("/") && !next.startsWith("//")) login.searchParams.set("next", next);
  const response = NextResponse.redirect(login);
  response.cookies.delete(SESSION_COOKIE);
  response.headers.set("Cache-Control", "no-store");
  return response;
}
