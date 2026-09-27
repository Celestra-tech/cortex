/**
 * Stateless operator sessions: the API key sealed in an httpOnly cookie.
 *
 * The dashboard keeps no database, so the key travels encrypted with
 * AES-256-GCM under a key derived (HKDF-SHA256) from DASHBOARD_SESSION_SECRET.
 * GCM authenticates the payload, so a tampered cookie fails to open rather
 * than yielding attacker-chosen data. Rotating the secret signs everyone out.
 */

export const SESSION_COOKIE = "cortex_session";

export interface Session {
  apiKey: string;
  organizationId: string;
  organizationName: string;
  organizationSlug: string;
  /** Unix seconds. */
  expiresAt: number;
}

const encoder = new TextEncoder();
const decoder = new TextDecoder();
const IV_BYTES = 12;
const keys = new Map<string, Promise<CryptoKey>>();

function deriveKey(secret: string): Promise<CryptoKey> {
  let key = keys.get(secret);
  if (!key) {
    key = crypto.subtle
      .importKey("raw", encoder.encode(secret), "HKDF", false, ["deriveKey"])
      .then((material) =>
        crypto.subtle.deriveKey(
          {
            name: "HKDF",
            hash: "SHA-256",
            salt: encoder.encode("celestra-cortex-dashboard"),
            info: encoder.encode("session-cookie-v1"),
          },
          material,
          { name: "AES-GCM", length: 256 },
          false,
          ["encrypt", "decrypt"],
        ),
      );
    keys.set(secret, key);
  }
  return key;
}

function toBase64Url(bytes: Uint8Array): string {
  return Buffer.from(bytes).toString("base64url");
}

function fromBase64Url(value: string): Uint8Array<ArrayBuffer> {
  return new Uint8Array(Buffer.from(value, "base64url"));
}

export async function sealSession(session: Session, secret: string): Promise<string> {
  const iv = crypto.getRandomValues(new Uint8Array(IV_BYTES));
  const plaintext = encoder.encode(JSON.stringify(session));
  const ciphertext = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv },
    await deriveKey(secret),
    plaintext,
  );
  return `${toBase64Url(iv)}.${toBase64Url(new Uint8Array(ciphertext))}`;
}

/** Null for missing, malformed, tampered, foreign-secret, or expired cookies. */
export async function openSession(
  sealed: string | undefined,
  secret: string,
  now: number = Date.now() / 1000,
): Promise<Session | null> {
  if (!sealed) return null;
  const [ivPart, dataPart, extra] = sealed.split(".");
  if (!ivPart || !dataPart || extra !== undefined) return null;
  try {
    const iv = fromBase64Url(ivPart);
    if (iv.length !== IV_BYTES) return null;
    const plaintext = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv },
      await deriveKey(secret),
      fromBase64Url(dataPart),
    );
    const session = JSON.parse(decoder.decode(plaintext)) as Partial<Session>;
    if (
      typeof session.apiKey !== "string" ||
      typeof session.organizationId !== "string" ||
      typeof session.expiresAt !== "number" ||
      session.expiresAt <= now
    ) {
      return null;
    }
    return session as Session;
  } catch {
    return null;
  }
}
