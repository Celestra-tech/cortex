import { expect, test } from "vitest";

import { openSession, sealSession, type Session } from "@/lib/session";

const SECRET = "a-test-secret-that-is-long-enough-123";
const SESSION: Session = {
  apiKey: "ctx_abcdefghijklmnopqrstuvwxyz",
  organizationId: "01900000-0000-7000-8000-000000000001",
  organizationName: "Acme",
  organizationSlug: "acme",
  expiresAt: 2_000_000_000,
};

test("a sealed session opens with the same secret", async () => {
  const sealed = await sealSession(SESSION, SECRET);
  expect(sealed).not.toContain(SESSION.apiKey);
  expect(await openSession(sealed, SECRET, 1_900_000_000)).toEqual(SESSION);
});

test("every seal is unique", async () => {
  expect(await sealSession(SESSION, SECRET)).not.toBe(await sealSession(SESSION, SECRET));
});

test("tampered, foreign, malformed, and expired cookies are rejected", async () => {
  const sealed = await sealSession(SESSION, SECRET);
  const [iv, data] = sealed.split(".") as [string, string];
  const flipped = `${iv}.${data.slice(0, -2)}${data.at(-2) === "A" ? "B" : "A"}${data.slice(-1)}`;

  expect(await openSession(flipped, SECRET, 1_900_000_000)).toBeNull();
  expect(await openSession(sealed, "another-secret-entirely-000000000", 1_900_000_000)).toBeNull();
  expect(await openSession("garbage", SECRET)).toBeNull();
  expect(await openSession(`${sealed}.extra`, SECRET)).toBeNull();
  expect(await openSession(undefined, SECRET)).toBeNull();
  expect(await openSession(sealed, SECRET, SESSION.expiresAt)).toBeNull();
});
