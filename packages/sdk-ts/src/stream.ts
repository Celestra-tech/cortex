import type { CompletionResponse } from "@celestra/cortex-types";

import { AbortError, CortexError, NetworkError, errorFromResponse } from "./errors";

export interface ServerSentEvent {
  event: string;
  data: string;
  id?: string;
}

/**
 * Parses a `text/event-stream` body per the WHATWG spec: any line ending,
 * multi-line `data`, comments, and events split across network chunks.
 */
export async function* parseSSE(body: ReadableStream<Uint8Array>): AsyncGenerator<ServerSentEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let event = "";
  let data: string[] = [];
  let id: string | undefined;

  function* dispatchLines(final: boolean): Generator<ServerSentEvent> {
    for (;;) {
      const match = /\r\n|\r|\n/.exec(buffer);
      // A trailing CR may be the first half of CRLF; wait for the next chunk.
      if (!match || (!final && match[0] === "\r" && match.index === buffer.length - 1)) return;
      const line = buffer.slice(0, match.index);
      buffer = buffer.slice(match.index + match[0].length);
      if (line === "") {
        if (data.length) yield { event: event || "message", data: data.join("\n"), id };
        event = "";
        data = [];
        continue;
      }
      if (line.startsWith(":")) continue;
      const colon = line.indexOf(":");
      const field = colon === -1 ? line : line.slice(0, colon);
      let value = colon === -1 ? "" : line.slice(colon + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "event") event = value;
      else if (field === "data") data.push(value);
      else if (field === "id" && !value.includes("\0")) id = value;
    }
  }

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      yield* dispatchLines(false);
    }
    buffer += decoder.decode();
    // An unterminated final event is incomplete and, per spec, discarded.
    yield* dispatchLines(true);
  } finally {
    reader.releaseLock();
  }
}

export interface StreamStartEvent {
  type: "start";
  id: string;
  createdAt: string;
  provider: string;
  model: string;
  routingReason: string;
}

export interface StreamTokenEvent {
  type: "token";
  index: number;
  delta: string;
}

export interface StreamCompleteEvent {
  type: "complete";
  completion: CompletionResponse;
}

export interface StreamErrorEvent {
  type: "error";
  error: CortexError;
}

export type ChatStreamEvent =
  StreamStartEvent | StreamTokenEvent | StreamCompleteEvent | StreamErrorEvent;

type Opener = (signal: AbortSignal) => Promise<Response>;

/**
 * A streamed completion. Iterate it for `start`, `token`, `complete`, and
 * `error` events, or use `textStream()` / `finalCompletion()`.
 *
 * Nothing is sent until consumption begins. Failures, including HTTP errors
 * before the stream opens, arrive as a final `error` event rather than a
 * throw; the helpers rethrow them. A stream can be consumed once.
 */
export class ChatStream implements AsyncIterable<ChatStreamEvent> {
  private readonly controller = new AbortController();
  private consumed = false;
  private result: CompletionResponse | null = null;

  constructor(
    private readonly open: Opener,
    private readonly operation: string,
    signal?: AbortSignal,
  ) {
    signal?.addEventListener("abort", () => this.controller.abort(signal.reason), { once: true });
    if (signal?.aborted) this.controller.abort(signal.reason);
  }

  /** The finished completion, once a `complete` event has been received. */
  get completion(): CompletionResponse | null {
    return this.result;
  }

  /** Stops the request; the iterator ends with an `AbortError` event. */
  abort(reason?: unknown): void {
    this.controller.abort(reason);
  }

  [Symbol.asyncIterator](): AsyncIterator<ChatStreamEvent> {
    if (this.consumed) throw new CortexError("A ChatStream can only be consumed once");
    this.consumed = true;
    return this.events();
  }

  /** Just the text deltas. Throws the stream's error, if any. */
  async *textStream(): AsyncGenerator<string> {
    for await (const event of this) {
      if (event.type === "token") yield event.delta;
      else if (event.type === "error") throw event.error;
    }
  }

  /** Consumes the stream and resolves with the finished completion. */
  async finalCompletion(): Promise<CompletionResponse> {
    for await (const event of this) {
      if (event.type === "error") throw event.error;
    }
    if (!this.result) throw new NetworkError(`${this.operation} ended without a completion`);
    return this.result;
  }

  private async *events(): AsyncGenerator<ChatStreamEvent> {
    let requestId: string | null = null;
    try {
      const response = await this.open(this.controller.signal);
      requestId = response.headers.get("x-request-id");
      const contentType = response.headers.get("content-type") ?? "";

      if (!contentType.includes("text/event-stream")) {
        // A server that ignored `stream` answered with plain JSON.
        this.result = (await response.json()) as CompletionResponse;
        yield { type: "complete", completion: this.result };
        return;
      }
      if (!response.body) throw new NetworkError(`${this.operation} returned no body`);

      for await (const message of parseSSE(response.body)) {
        const event = this.decode(message, response.headers, requestId);
        if (!event) continue;
        yield event;
        if (event.type === "complete") {
          this.result = event.completion;
          return;
        }
        if (event.type === "error") return;
      }
      throw new NetworkError(`${this.operation} stream ended before completing`, { requestId });
    } catch (error) {
      yield { type: "error", error: this.normalize(error, requestId) };
    } finally {
      this.controller.abort();
    }
  }

  private decode(
    message: ServerSentEvent,
    headers: Headers,
    requestId: string | null,
  ): ChatStreamEvent | null {
    let data: Record<string, unknown>;
    try {
      data = JSON.parse(message.data) as Record<string, unknown>;
    } catch {
      return null;
    }
    switch (message.event) {
      case "start":
        return {
          type: "start",
          id: String(data.id),
          createdAt: String(data.created_at),
          provider: String(data.provider),
          model: String(data.model),
          routingReason: String(data.routing_reason ?? ""),
        };
      case "token":
        return { type: "token", index: Number(data.index), delta: String(data.delta ?? "") };
      case "complete":
        return { type: "complete", completion: data as unknown as CompletionResponse };
      case "error": {
        const status = typeof data.status === "number" ? data.status : 502;
        return {
          type: "error",
          error: errorFromResponse(status, data, headers, requestId, this.operation),
        };
      }
      default:
        return null;
    }
  }

  private normalize(error: unknown, requestId: string | null): CortexError {
    // API errors and explicit aborts are already precise.
    if (error instanceof CortexError && !(error instanceof NetworkError)) return error;
    // A read that failed because we aborted is an abort, not a network fault.
    if (this.controller.signal.aborted) {
      return new AbortError(`${this.operation} was aborted`, {
        cause: this.controller.signal.reason,
      });
    }
    if (error instanceof CortexError) return error;
    return new NetworkError(`${this.operation} stream failed: ${String(error)}`, {
      cause: error,
      requestId,
    });
  }
}
