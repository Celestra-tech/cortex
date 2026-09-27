import { Cortex } from "@celestra/cortex-sdk";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { getServerConfig } from "@/lib/config";
import { openSession, SESSION_COOKIE, type Session } from "@/lib/session";

const API_TIMEOUT_MS = 10_000;

export async function getSession(): Promise<Session | null> {
  const store = await cookies();
  return openSession(store.get(SESSION_COOKIE)?.value, getServerConfig().sessionSecret);
}

/** The signed-in session, or a redirect to the sign-in page. */
export async function requireSession(next = "/executions"): Promise<Session> {
  const session = await getSession();
  if (!session) redirect(`/login?next=${encodeURIComponent(next)}`);
  return session;
}

/** A client acting as the session's organization. Server-side only: it holds the key. */
export function cortexFor(session: Pick<Session, "apiKey">): Cortex {
  return new Cortex({
    baseURL: getServerConfig().apiUrl,
    apiKey: session.apiKey,
    organizationId: null,
    timeoutMs: API_TIMEOUT_MS,
    maxRetries: 1,
  });
}
