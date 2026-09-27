import { Button } from "@celestra/cortex-ui/components/button";
import Link from "next/link";

import { signOut } from "@/app/login/actions";
import type { Session } from "@/lib/session";

const SECTIONS = [
  { href: "/executions", label: "Executions" },
  { href: "/evidence", label: "Evidence" },
] as const;

export function AppHeader({
  session,
  current,
}: {
  session: Pick<Session, "organizationId" | "organizationName">;
  current: (typeof SECTIONS)[number]["href"];
}) {
  return (
    <header className="mx-auto flex w-full max-w-6xl items-center justify-between gap-4 px-6 py-6 sm:px-10">
      <div className="flex items-center gap-8">
        <Link href="/" className="text-[13px] font-semibold tracking-[0.2em]">
          CELESTRA
        </Link>
        <nav className="flex items-center gap-5 text-sm" aria-label="Sections">
          {SECTIONS.map((section) => (
            <Link
              key={section.href}
              href={section.href}
              aria-current={section.href === current ? "page" : undefined}
              className={section.href === current ? "font-medium" : "text-muted-foreground"}
            >
              {section.label}
            </Link>
          ))}
        </nav>
      </div>
      <div className="flex items-center gap-4 text-sm">
        <span className="text-muted-foreground" title={session.organizationId}>
          {session.organizationName}
        </span>
        <form action={signOut}>
          <Button type="submit" variant="link" size="sm" className="h-auto px-0">
            Sign out
          </Button>
        </form>
      </div>
    </header>
  );
}
