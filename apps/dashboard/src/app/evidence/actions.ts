"use server";

import {
  AuthenticationError,
  CortexError,
  type EvidenceNodeDetail,
  NotFoundError,
} from "@celestra/cortex-sdk";

import { cortexFor, getSession } from "@/lib/auth";

export type NodeInspection =
  { ok: true; detail: EvidenceNodeDetail } | { ok: false; error: string };

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** One node's neighbors and the decisions it fed, as the signed-in organization. */
export async function inspectNode(nodeId: string): Promise<NodeInspection> {
  if (typeof nodeId !== "string" || !UUID_PATTERN.test(nodeId)) {
    return { ok: false, error: "Not an evidence node id." };
  }
  const session = await getSession();
  if (!session) return { ok: false, error: "Your session has expired. Sign in again." };
  try {
    return { ok: true, detail: await cortexFor(session).evidence.node(nodeId) };
  } catch (error) {
    if (error instanceof NotFoundError) return { ok: false, error: "This node no longer exists." };
    if (error instanceof AuthenticationError) {
      return { ok: false, error: "Your API key was revoked. Sign in again." };
    }
    if (error instanceof CortexError) return { ok: false, error: "The Cortex API is unavailable." };
    throw error;
  }
}
