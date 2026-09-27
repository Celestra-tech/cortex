import type { Metadata } from "next";
import Link from "next/link";

import { SignInForm } from "./sign-in-form";

export const metadata: Metadata = { title: "Sign in" };

const NOTICES: Record<string, string> = {
  expired: "Your session ended or the key was revoked. Sign in again.",
};

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; reason?: string }>;
}) {
  const { next, reason } = await searchParams;
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="mx-auto flex w-full max-w-6xl items-center justify-between px-6 py-6 sm:px-10">
        <Link href="/" className="text-[13px] font-semibold tracking-[0.2em]">
          CELESTRA
        </Link>
      </header>
      <main className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center px-6 pb-24">
        <h1 className="text-3xl font-semibold tracking-[-0.03em]">Sign in to Cortex</h1>
        <p className="text-muted-foreground mt-2 mb-8 text-sm">
          Use an organization API key to observe its executions.
        </p>
        <SignInForm next={next ?? "/executions"} notice={(reason && NOTICES[reason]) || null} />
      </main>
    </div>
  );
}
