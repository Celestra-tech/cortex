import type { CortexEvent } from "@celestra/cortex-types";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CortexEventStream, eventsUrl, parseStreamFrame, type StreamStatus } from "../src/events";

class FakeSocket {
  static instances: FakeSocket[] = [];
  readyState = 0;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  closedWith: number | null = null;

  constructor(
    readonly url: string,
    readonly protocols?: string[],
  ) {
    FakeSocket.instances.push(this);
  }

  close(code = 1000): void {
    this.closedWith = code;
  }

  send(frame: unknown): void {
    this.onmessage?.({ data: JSON.stringify(frame) } as MessageEvent);
  }

  drop(code = 1006): void {
    this.onclose?.({ code } as CloseEvent);
  }
}

const READY = { type: "stream.ready", data: { organization_id: "org" } };
const EVENT: CortexEvent = {
  id: "evt-1",
  type: "document.deleted",
  organization_id: "org",
  occurred_at: "2026-09-27T12:00:00+00:00",
  data: { document_id: "doc-1", title: "Guide" },
};

function stream(overrides: Partial<ConstructorParameters<typeof CortexEventStream>[0]> = {}) {
  const events: CortexEvent[] = [];
  const statuses: StreamStatus[] = [];
  const readies: boolean[] = [];
  const instance = new CortexEventStream({
    url: "ws://cortex.test/v1/events?organization_id=org",
    WebSocket: FakeSocket,
    onEvent: (event) => events.push(event),
    onStatus: (status) => statuses.push(status),
    onReady: ({ reconnected }) => readies.push(reconnected),
    random: () => 1,
    minBackoffMs: 100,
    maxBackoffMs: 1000,
    staleAfterMs: 5000,
    ...overrides,
  });
  const socket = () => FakeSocket.instances.at(-1)!;
  return { instance, events, statuses, readies, socket };
}

beforeEach(() => {
  FakeSocket.instances = [];
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("eventsUrl", () => {
  it("maps http(s) to ws(s) and carries the organization", () => {
    expect(eventsUrl("http://localhost:8000/", "org 1")).toBe(
      "ws://localhost:8000/v1/events?organization_id=org+1",
    );
    expect(eventsUrl("https://api.celestra.ai", "o")).toBe(
      "wss://api.celestra.ai/v1/events?organization_id=o",
    );
  });

  it("omits the organization when none is given", () => {
    expect(eventsUrl("https://api.celestra.ai", null)).toBe("wss://api.celestra.ai/v1/events");
  });
});

describe("parseStreamFrame", () => {
  it("accepts control frames and known events", () => {
    expect(parseStreamFrame(JSON.stringify(READY))).toEqual(READY);
    expect(parseStreamFrame(JSON.stringify(EVENT))).toEqual(EVENT);
  });

  it.each([
    ["not json", "{"],
    ["a scalar", "42"],
    ["an unknown type", JSON.stringify({ ...EVENT, type: "billing.charged" })],
    ["a missing id", JSON.stringify({ ...EVENT, id: undefined })],
    ["missing data", JSON.stringify({ type: "stream.ping" })],
  ])("rejects %s", (_label, raw) => {
    expect(parseStreamFrame(raw)).toBeNull();
  });
});

describe("CortexEventStream", () => {
  it("opens on stream.ready and forwards events but not heartbeats", () => {
    const { instance, events, statuses, readies, socket } = stream();
    instance.start();
    expect(statuses).toEqual(["connecting"]);

    socket().send(READY);
    socket().send({ type: "stream.ping", data: {} });
    socket().send(EVENT);
    socket().send({ type: "unknown", data: {} });

    expect(instance.status).toBe("open");
    expect(readies).toEqual([false]);
    expect(events).toEqual([EVENT]);
  });

  it("reconnects with exponential backoff and flags the reconnect", () => {
    const { instance, statuses, readies, socket } = stream();
    instance.start();
    socket().send(READY);

    socket().drop();
    expect(instance.status).toBe("reconnecting");
    vi.advanceTimersByTime(99);
    expect(FakeSocket.instances).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(FakeSocket.instances).toHaveLength(2);

    socket().drop();
    vi.advanceTimersByTime(199);
    expect(FakeSocket.instances).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(FakeSocket.instances).toHaveLength(3);

    socket().send(READY);
    expect(readies).toEqual([false, true]);
    expect(statuses).toEqual(["connecting", "open", "reconnecting", "open"]);
  });

  it("caps the backoff", () => {
    const { instance, socket } = stream();
    instance.start();
    for (let i = 0; i < 8; i++) {
      socket().drop();
      vi.advanceTimersByTime(1000);
    }
    expect(FakeSocket.instances).toHaveLength(9);
  });

  it("gives up when the server refuses the stream", () => {
    const { instance, socket } = stream();
    instance.start();
    socket().drop(1008);
    vi.advanceTimersByTime(60_000);
    expect(instance.status).toBe("refused");
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it("treats silence past the heartbeat as a dead socket", () => {
    const { instance, socket } = stream();
    instance.start();
    socket().send(READY);
    vi.advanceTimersByTime(4999);
    socket().send({ type: "stream.ping", data: {} });
    vi.advanceTimersByTime(4999);
    expect(FakeSocket.instances).toHaveLength(1);

    vi.advanceTimersByTime(1);
    expect(FakeSocket.instances[0]!.closedWith).toBe(4000);
    expect(instance.status).toBe("reconnecting");
    vi.advanceTimersByTime(100);
    expect(FakeSocket.instances).toHaveLength(2);
  });

  it("stops cleanly and ignores late frames", () => {
    const { instance, events, statuses, socket } = stream();
    instance.start();
    const first = socket();
    first.send(READY);
    instance.stop();

    expect(first.closedWith).toBe(1000);
    first.send(EVENT);
    vi.advanceTimersByTime(60_000);
    expect(events).toEqual([]);
    expect(FakeSocket.instances).toHaveLength(1);
    expect(statuses.at(-1)).toBe("closed");
  });

  it("offers the key as a subprotocol and re-resolves it on reconnect", async () => {
    const keys = ["ctx_first", "ctx_second"];
    const { instance, socket } = stream({ apiKey: () => keys.shift() ?? null });
    instance.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(socket().protocols).toEqual(["cortex.events.v1", "cortex.bearer.ctx_first"]);

    socket().drop();
    await vi.advanceTimersByTimeAsync(100);
    expect(FakeSocket.instances).toHaveLength(2);
    expect(socket().protocols).toEqual(["cortex.events.v1", "cortex.bearer.ctx_second"]);
  });

  it("connects without subprotocols when there is no key", () => {
    const { instance, socket } = stream();
    instance.start();
    expect(socket().protocols).toBeUndefined();
  });

  it("does not connect when stopped while the key resolves", async () => {
    const { instance } = stream({ apiKey: async () => "ctx_key" });
    instance.start();
    instance.stop();
    await vi.advanceTimersByTimeAsync(0);
    expect(FakeSocket.instances).toHaveLength(0);
  });

  it("retries when the key source fails", async () => {
    let calls = 0;
    const { instance, socket } = stream({
      apiKey: () => {
        calls += 1;
        if (calls === 1) throw new Error("vault unavailable");
        return "ctx_key";
      },
    });
    instance.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(instance.status).toBe("reconnecting");
    expect(FakeSocket.instances).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(100);
    expect(socket().protocols).toEqual(["cortex.events.v1", "cortex.bearer.ctx_key"]);
  });
});
