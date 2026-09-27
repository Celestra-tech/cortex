"use server";

import { AuthenticationError, CortexError, RateLimitError } from "@celestra/cortex-sdk";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { cortexFor } from "@/lib/auth";
import { getServerConfig } from "@/lib/config";
import { SESSION_COOKIE, sealSession } from "@/lib/session";

export interface SignInState {
  error: string | null;
}

const KEY_PATTERN = /^ctx_[A-Za-z0-9_-]{20,}$/;

/** Only same-site paths, so `next` cannot bounce the operator to another origin. */
function safeNext(value: FormDataEntryValue | null): string {
  const next = typeof value === "string" ? value : "";
  return next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/\\")
    ? next
    : "/executions";
}

export async function signIn(_previous: SignInState, form: FormData): Promise<SignInState> {
  const apiKey = String(form.get("apiKey") ?? "").trim();
  if (!KEY_PATTERN.test(apiKey)) {
    return { error: "Enter a Cortex API key (it starts with ctx_)." };
  }

  const config = getServerConfig();
  let organization;
  try {
    organization = await cortexFor({ apiKey }).observatory.organization();
  } catch (error) {
    if (error instanceof AuthenticationError)
      return { error: "That API key is invalid or revoked." };
    if (error instanceof RateLimitError) return { error: "Too many attempts. Try again shortly." };
    if (error instanceof CortexError) {
      return { error: "The Cortex API could not be reached. Check that it is running." };
    }
    throw error;
  }

  const expiresAt = Math.floor(Date.now() / 1000) + config.sessionTtlSeconds;
  const sealed = await sealSession(
    {
      apiKey,
      organizationId: organization.id,
      organizationName: organization.name,
      organizationSlug: organization.slug,
      expiresAt,
    },
    config.sessionSecret,
  );
  (await cookies()).set(SESSION_COOKIE, sealed, {
    httpOnly: true,
    secure: config.secureCookies,
    sameSite: "lax",
    path: "/",
    maxAge: config.sessionTtlSeconds,
  });
  redirect(safeNext(form.get("next")));
}

export async function signOut(): Promise<void> {
  (await cookies()).delete(SESSION_COOKIE);
  redirect("/login");
}
