import { type NextRequest, NextResponse } from "next/server";

import { getServerConfig } from "@/lib/config";
import { openSession, SESSION_COOKIE } from "@/lib/session";

/**
 * Sends signed-out visitors of operator pages to sign-in before anything
 * renders. Pages re-check the session themselves; this is the fast path.
 */
export async function proxy(request: NextRequest) {
  const session = await openSession(
    request.cookies.get(SESSION_COOKIE)?.value,
    getServerConfig().sessionSecret,
  );
  if (session) return NextResponse.next();

  const login = new URL("/login", request.url);
  login.searchParams.set("next", request.nextUrl.pathname + request.nextUrl.search);
  const response = NextResponse.redirect(login);
  if (request.cookies.has(SESSION_COOKIE)) response.cookies.delete(SESSION_COOKIE);
  return response;
}

export const config = {
  matcher: ["/executions/:path*", "/evidence/:path*"],
};
