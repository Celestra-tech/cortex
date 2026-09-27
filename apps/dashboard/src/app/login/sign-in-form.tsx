"use client";

import { Button } from "@celestra/cortex-ui/components/button";
import { useActionState } from "react";

import { signIn, type SignInState } from "./actions";

const INITIAL: SignInState = { error: null };

export function SignInForm({ next, notice }: { next: string; notice: string | null }) {
  const [state, action, pending] = useActionState(signIn, INITIAL);
  const message = state.error ?? notice;

  return (
    <form action={action} className="flex flex-col gap-4" noValidate>
      <input type="hidden" name="next" value={next} />
      <label htmlFor="apiKey" className="text-sm font-medium">
        API key
      </label>
      <input
        id="apiKey"
        name="apiKey"
        type="password"
        autoComplete="off"
        spellCheck={false}
        required
        placeholder="ctx_…"
        aria-invalid={state.error ? true : undefined}
        aria-describedby={message ? "sign-in-message" : undefined}
        className="border-input bg-background focus-visible:ring-ring h-10 rounded-md border px-3 font-mono text-sm outline-none focus-visible:ring-2"
      />
      {message ? (
        <p id="sign-in-message" role="alert" className="text-muted-foreground text-sm">
          {message}
        </p>
      ) : null}
      <Button type="submit" disabled={pending}>
        {pending ? "Verifying…" : "Sign in"}
      </Button>
      <p className="text-muted-foreground text-xs">
        The key is verified against the API, then kept encrypted in an httpOnly cookie on this
        dashboard. Create keys with <code className="font-mono">cortex_api.cli create-api-key</code>{" "}
        or <code className="font-mono">POST /v1/api-keys</code>.
      </p>
    </form>
  );
}
