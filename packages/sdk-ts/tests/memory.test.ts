import { describe, expect, it } from "vitest";

import { ValidationError } from "../src";
import { ORG, cortex, json, mockFetch } from "./helpers";

const CONVERSATION = {
  id: "conv-1",
  organization_id: ORG,
  title: "Support",
  created_at: "2026-09-27T12:00:00+00:00",
  updated_at: "2026-09-27T12:00:00+00:00",
};

const MESSAGE = {
  id: "msg-1",
  conversation_id: "conv-1",
  role: "user",
  content: "Hi",
  token_count: 1,
  metadata: {},
  created_at: "2026-09-27T12:00:01+00:00",
};

describe("memory", () => {
  it("creates conversations and appends messages", async () => {
    const { fetch, calls } = mockFetch(json(CONVERSATION, 201), json(MESSAGE, 201));
    const client = cortex(fetch);

    const conversation = await client.memory.createConversation({ title: "Support" });
    const message = await client.memory.addMessage({
      conversation_id: conversation.id,
      role: "user",
      content: "Hi",
    });

    expect(message.id).toBe("msg-1");
    expect(calls[0]!.url.pathname).toBe("/v1/conversations");
    expect(calls[0]!.body).toEqual({ title: "Support" });
    expect(calls[1]!.url.pathname).toBe("/v1/messages");
    expect(calls[1]!.body).toEqual({ conversation_id: "conv-1", role: "user", content: "Hi" });
  });

  it("validates messages before sending", async () => {
    const { fetch, calls } = mockFetch();
    await expect(
      cortex(fetch).memory.addMessage({ conversation_id: "conv-1", role: "user", content: "" }),
    ).rejects.toBeInstanceOf(ValidationError);
    expect(calls).toHaveLength(0);
  });

  it("maps context parameters to the query string", async () => {
    const { fetch, calls } = mockFetch(
      json({ conversation_id: "conv/1", messages: [], memories: [] }),
    );
    await cortex(fetch).memory.getContext("conv/1", { limit: 20, memoryLimit: 0 });
    expect(calls[0]!.url.pathname).toBe("/v1/conversations/conv%2F1/context");
    expect(calls[0]!.url.search).toBe("?limit=20&memory_limit=0");
  });

  it("iterates every conversation across pages", async () => {
    const summary = (id: string) => ({ ...CONVERSATION, id, message_count: 0, session: "cold" });
    const { fetch, calls } = mockFetch(
      json({ items: [summary("a"), summary("b")], total: 3, limit: 2, offset: 0 }),
      json({ items: [summary("c")], total: 3, limit: 2, offset: 2 }),
    );
    const ids = [];
    for await (const conversation of cortex(fetch).memory.iterConversations({ limit: 2 })) {
      ids.push(conversation.id);
    }
    expect(ids).toEqual(["a", "b", "c"]);
    expect(calls.map((c) => c.url.searchParams.get("offset"))).toEqual(["0", "2"]);
  });

  it("deletes conversations and searches memories", async () => {
    const { fetch, calls } = mockFetch(
      new Response(null, { status: 204 }),
      json({ query: "tone", memories: [] }),
    );
    const client = cortex(fetch);
    await expect(client.memory.deleteConversation("conv-1")).resolves.toBeUndefined();
    await client.memory.searchMemories({ query: "tone", types: ["preference", "semantic"] });
    expect(calls[0]!.method).toBe("DELETE");
    expect(calls[1]!.url.search).toBe("?query=tone&type=preference&type=semantic");
  });

  it("stores long-term memories", async () => {
    const memory = {
      id: "mem-1",
      organization_id: ORG,
      type: "preference",
      summary: "Prefers short answers",
      content: "The user prefers short answers.",
      importance: 0.8,
      source_message_id: null,
      created_at: "2026-09-27T12:00:00+00:00",
      updated_at: "2026-09-27T12:00:00+00:00",
    };
    const { fetch, calls } = mockFetch(json(memory, 201));
    const stored = await cortex(fetch).memory.storeMemory({
      type: "preference",
      content: memory.content,
      importance: 0.8,
    });
    expect(stored.summary).toBe("Prefers short answers");
    expect(calls[0]!.url.pathname).toBe("/v1/memories");
  });
});
