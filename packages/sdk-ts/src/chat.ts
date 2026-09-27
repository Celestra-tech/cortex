import type { CompletionRequest, CompletionResponse } from "@celestra/cortex-types";

import type { RequestOptions } from "./http";
import { APIResource } from "./resource";
import { ChatStream } from "./stream";
import { completionRequestSchema, completionResponseSchema, validateInput } from "./types";

export type ChatParams = Omit<CompletionRequest, "stream">;

/** Routed completions: Cortex picks the provider, retries, and falls back server-side. */
export class Chat extends APIResource {
  /**
   * A complete answer. Throws `ProviderError` when every provider failed; its
   * `attempts` list each call the server made.
   */
  async complete(params: ChatParams, options?: RequestOptions): Promise<CompletionResponse> {
    validateInput("chat.complete", completionRequestSchema, params);
    return this.transport.request({
      operation: "chat.complete",
      method: "POST",
      path: "/v1/chat/completions",
      json: { ...params, stream: false },
      schema: completionResponseSchema,
      options,
    });
  }

  /**
   * The same completion as Server-Sent Events. Nothing is sent until the
   * stream is consumed.
   *
   * ```ts
   * for await (const event of cortex.chat.stream({ messages })) {
   *   if (event.type === "token") process.stdout.write(event.delta);
   * }
   * ```
   */
  stream(params: ChatParams, options: RequestOptions = {}): ChatStream {
    validateInput("chat.stream", completionRequestSchema, params);
    return new ChatStream(
      (signal) =>
        this.transport.send({
          operation: "chat.stream",
          method: "POST",
          path: "/v1/chat/completions",
          json: { ...params, stream: true },
          options: {
            ...options,
            signal,
            headers: { Accept: "text/event-stream", ...options.headers },
          },
        }),
      "chat.stream",
      options.signal,
    );
  }
}
