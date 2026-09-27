import { describe, expect, it } from "vitest";

import {
  AbortError,
  AuthenticationError,
  type ChatStreamEvent,
  CortexError,
  NetworkError,
  ProviderError,
  parseSSE,
} from "../src";
import { HELLO, completion, cortex, hang, json, mockFetch, sse } from "./helpers";

function frame(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

const DONE = completion({ output: "Hello there, operator." });
const START = {
  id: DONE.id,
  created_at: DONE.created_at,
  provider: "anthropic",
  model: "claude-haiku-4-5",
  routing_reason: "auto: best balanced score",
};
const FRAMES = [
  frame("start", START),
  frame("token", { index: 0, delta: "Hello " }),
  frame("token", { index: 1, delta: "there, " }),
  frame("token", { index: 2, delta: "operator." }),
  frame("complete", DONE),
];

async function collect(events: AsyncIterable<ChatStreamEvent>): Promise<ChatStreamEvent[]> {
  const out: ChatStreamEvent[] = [];
  for await (const event of events) out.push(event);
  return out;
}

async function parse(chunks: string[]) {
  const events = [];
  for await (const event of parseSSE(sse(chunks).body!)) events.push(event);
  return events;
}

describe("parseSSE", () => {
  it("reassembles events split across chunks", async () => {
    const text = FRAMES.join("");
    const chunks = text.match(/[\s\S]{1,7}/g)!;
    const events = await parse(chunks);
    expect(events.map((e) => e.event)).toEqual(["start", "token", "token", "token", "complete"]);
    expect(JSON.parse(events[4]!.data)).toEqual(DONE);
  });

  it("handles CRLF split across chunks, comments, and multi-line data", async () => {
    const events = await parse([
      ": keep-alive\r",
      "\n",
      "event: note\r\ndata: line one\r",
      "\ndata: line two\r\nid: 7\r\n\r\n",
      "data: unnamed\n\n",
    ]);
    expect(events).toEqual([
      { event: "note", data: "line one\nline two", id: "7" },
      { event: "message", data: "unnamed", id: "7" },
    ]);
  });

  it("discards an unterminated trailing event", async () => {
    expect(await parse(["data: complete\n\n", "data: partial"])).toHaveLength(1);
  });
});

describe("chat.stream", () => {
  it("yields start, tokens, and complete", async () => {
    const { fetch, calls } = mockFetch(sse(FRAMES));
    const events = await collect(cortex(fetch).chat.stream(HELLO));

    expect(events.map((e) => e.type)).toEqual(["start", "token", "token", "token", "complete"]);
    expect(events[0]).toMatchObject({ type: "start", provider: "anthropic", id: DONE.id });
    expect(calls[0]!.body).toMatchObject({ stream: true });
    expect(calls[0]!.headers.get("accept")).toBe("text/event-stream");
  });

  it("is lazy: nothing is sent until consumption", async () => {
    const { fetch, calls } = mockFetch(sse(FRAMES));
    const stream = cortex(fetch).chat.stream(HELLO);
    expect(calls).toHaveLength(0);
    await stream.finalCompletion();
    expect(calls).toHaveLength(1);
  });

  it("offers text deltas and the final completion", async () => {
    const { fetch } = mockFetch(sse(FRAMES), sse(FRAMES));
    const client = cortex(fetch);

    let text = "";
    for await (const delta of client.chat.stream(HELLO).textStream()) text += delta;
    expect(text).toBe(DONE.output);

    const stream = client.chat.stream(HELLO);
    const final = await stream.finalCompletion();
    expect(final).toEqual(DONE);
    expect(stream.completion).toEqual(DONE);
  });

  it("reports HTTP failures as an error event, and helpers rethrow", async () => {
    const { fetch } = mockFetch(
      json({ detail: "Invalid or revoked API key" }, 401),
      json({ id: "c1", detail: "All providers failed", attempts: [] }, 502),
    );
    const client = cortex(fetch);

    const events = await collect(client.chat.stream(HELLO));
    expect(events).toHaveLength(1);
    expect(events[0]!.type).toBe("error");
    expect((events[0] as { error: unknown }).error).toBeInstanceOf(AuthenticationError);

    await expect(client.chat.stream(HELLO).finalCompletion()).rejects.toBeInstanceOf(ProviderError);
  });

  it("maps a server error event", async () => {
    const { fetch } = mockFetch(
      sse([frame("start", START), frame("error", { status: 502, detail: "upstream dropped" })]),
    );
    const events = await collect(cortex(fetch).chat.stream(HELLO));
    const last = events.at(-1)!;
    expect(last.type).toBe("error");
    const error = (last as { error: unknown }).error as ProviderError;
    expect(error).toBeInstanceOf(ProviderError);
    expect(error.detail).toBe("upstream dropped");
  });

  it("treats a stream that ends early as a network error", async () => {
    const { fetch } = mockFetch(sse(FRAMES.slice(0, 2)));
    await expect(cortex(fetch).chat.stream(HELLO).finalCompletion()).rejects.toBeInstanceOf(
      NetworkError,
    );
  });

  it("retries opening the stream after a 503", async () => {
    const { fetch, calls } = mockFetch(json({}, 503), sse(FRAMES));
    const final = await cortex(fetch).chat.stream(HELLO).finalCompletion();
    expect(final.id).toBe(DONE.id);
    expect(calls).toHaveLength(2);
  });

  it("can be aborted", async () => {
    const { fetch } = mockFetch(hang);
    const stream = cortex(fetch).chat.stream(HELLO);
    setTimeout(() => stream.abort(), 5);
    const events = await collect(stream);
    expect((events[0] as { error: unknown }).error).toBeInstanceOf(AbortError);
  });

  it("accepts a plain JSON answer from servers that do not stream", async () => {
    const { fetch } = mockFetch(json(DONE));
    const events = await collect(cortex(fetch).chat.stream(HELLO));
    expect(events).toEqual([{ type: "complete", completion: DONE }]);
  });

  it("can only be consumed once", async () => {
    const { fetch } = mockFetch(sse(FRAMES));
    const stream = cortex(fetch).chat.stream(HELLO);
    await collect(stream);
    expect(() => stream[Symbol.asyncIterator]()).toThrow(CortexError);
  });
});
