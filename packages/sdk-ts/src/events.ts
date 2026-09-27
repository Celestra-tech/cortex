import type { CortexEvent, CortexEventType, StreamFrame } from "@celestra/cortex-types";

/**
 * `connecting`: first attempt. `open`: `stream.ready` received.
 * `reconnecting`: waiting to retry after a drop. `refused`: the server rejected
 * the stream (unknown organization or origin) and retrying would not help.
 * `closed`: stopped by the caller.
 */
export type StreamStatus = "connecting" | "open" | "reconnecting" | "refused" | "closed";

/** Close codes after which reconnecting is pointless. */
const TERMINAL_CLOSE_CODES = new Set([1008]);

/** Offered on every connection; the server answers with it when it accepts a key. */
export const EVENTS_PROTOCOL = "cortex.events.v1";
/** Browsers cannot set headers on a WebSocket, so the key rides in a subprotocol. */
export const BEARER_PROTOCOL_PREFIX = "cortex.bearer.";

const EVENT_TYPES: ReadonlySet<string> = new Set<CortexEventType>([
  "request.received",
  "execution.completed",
  "execution.failed",
  "document.ingesting",
  "document.indexed",
  "document.failed",
  "document.deleted",
  "memory.conversation_created",
  "memory.conversation_deleted",
  "memory.message_appended",
  "memory.stored",
]);

/** `ws(s)://…/v1/events[?organization_id=…]` for an API base URL. */
export function eventsUrl(baseUrl: string, organizationId?: string | null): string {
  const url = new URL(`${baseUrl.replace(/\/+$/, "")}/v1/events`);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  if (organizationId) url.searchParams.set("organization_id", organizationId);
  return url.toString();
}

/** Subprotocols that authenticate a stream with `apiKey`. */
export function eventsProtocols(apiKey: string): string[] {
  return [EVENTS_PROTOCOL, `${BEARER_PROTOCOL_PREFIX}${apiKey}`];
}

/** Parses one frame; null for anything that is not a known frame shape. */
export function parseStreamFrame(raw: string): StreamFrame | null {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof value !== "object" || value === null) return null;
  const { type, data } = value as { type?: unknown; data?: unknown };
  if (typeof type !== "string" || typeof data !== "object" || data === null) return null;
  if (type === "stream.ready" || type === "stream.ping") return value as StreamFrame;
  if (!EVENT_TYPES.has(type)) return null;
  const { id, organization_id, occurred_at } = value as Record<string, unknown>;
  if (typeof id !== "string" || typeof organization_id !== "string") return null;
  if (typeof occurred_at !== "string") return null;
  return value as CortexEvent;
}

type WebSocketLike = Pick<WebSocket, "close" | "readyState"> & {
  onopen: ((event: Event) => void) | null;
  onmessage: ((event: MessageEvent) => void) | null;
  onclose: ((event: CloseEvent) => void) | null;
  onerror: ((event: Event) => void) | null;
};

export interface EventStreamOptions {
  /** Full stream URL; see `eventsUrl`. */
  url: string;
  onEvent: (event: CortexEvent) => void;
  onStatus?: (status: StreamStatus) => void;
  /**
   * Called on every `stream.ready`. Pub/sub keeps no history, so after a
   * reconnect (`reconnected: true`) callers should reload current state.
   */
  onReady?: (info: { reconnected: boolean }) => void;
  /**
   * Resolved before every connection attempt, so rotating keys are picked up
   * on reconnect. Omit for development servers that trust `organization_id`.
   */
  apiKey?: () => string | null | Promise<string | null>;
  /** Defaults to the global `WebSocket`. */
  WebSocket?: new (url: string, protocols?: string[]) => WebSocketLike;
  minBackoffMs?: number;
  maxBackoffMs?: number;
  /** Reconnect when nothing (not even a heartbeat) arrives for this long. Defaults to 45000ms. */
  staleAfterMs?: number;
  /** Jitter source, for tests. */
  random?: () => number;
}

/**
 * A self-healing subscription to `/v1/events`: reconnects with jittered
 * exponential backoff, treats silence past the heartbeat as a dead socket,
 * and gives up only when the server refuses the stream.
 */
export class CortexEventStream {
  private socket: WebSocketLike | null = null;
  private attempt = 0;
  private everOpened = false;
  private stopped = true;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private staleTimer: ReturnType<typeof setTimeout> | null = null;
  private currentStatus: StreamStatus = "closed";
  /** Invalidates key lookups still pending when the stream stops or restarts. */
  private generation = 0;

  constructor(private readonly options: EventStreamOptions) {}

  get status(): StreamStatus {
    return this.currentStatus;
  }

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.attempt = 0;
    this.setStatus(this.everOpened ? "reconnecting" : "connecting");
    this.connect();
  }

  stop(): void {
    this.stopped = true;
    this.generation += 1;
    this.clearTimers();
    const socket = this.socket;
    this.socket = null;
    if (socket) {
      this.detach(socket);
      socket.close(1000, "client stopped");
    }
    this.setStatus("closed");
  }

  private connect(): void {
    const Socket =
      this.options.WebSocket ?? (globalThis.WebSocket as EventStreamOptions["WebSocket"]);
    if (!Socket) {
      this.setStatus("refused");
      return;
    }
    const { apiKey } = this.options;
    if (!apiKey) {
      this.open(Socket, undefined);
      return;
    }
    const generation = ++this.generation;
    Promise.resolve()
      .then(apiKey)
      .then(
        (key) => {
          if (this.stopped || generation !== this.generation) return;
          this.open(Socket, key ? eventsProtocols(key) : undefined);
        },
        () => {
          // A failing key source is retried like a dropped connection.
          if (!this.stopped && generation === this.generation) this.scheduleRetry();
        },
      );
  }

  private open(
    Socket: NonNullable<EventStreamOptions["WebSocket"]>,
    protocols: string[] | undefined,
  ): void {
    const socket = protocols
      ? new Socket(this.options.url, protocols)
      : new Socket(this.options.url);
    this.socket = socket;
    this.armStaleTimer();

    socket.onmessage = (message) => {
      if (typeof message.data !== "string") return;
      const frame = parseStreamFrame(message.data);
      if (!frame) return;
      this.armStaleTimer();
      if (frame.type === "stream.ready") {
        const reconnected = this.everOpened;
        this.everOpened = true;
        this.attempt = 0;
        this.setStatus("open");
        this.options.onReady?.({ reconnected });
      } else if (frame.type !== "stream.ping") {
        this.options.onEvent(frame);
      }
    };
    socket.onclose = (event) => {
      this.detach(socket);
      if (this.socket !== socket) return;
      this.socket = null;
      if (TERMINAL_CLOSE_CODES.has(event.code)) {
        this.clearTimers();
        this.stopped = true;
        this.setStatus("refused");
        return;
      }
      this.scheduleRetry();
    };
    // Errors are always followed by a close; retry is handled there.
    socket.onerror = () => undefined;
  }

  private scheduleRetry(): void {
    if (this.stopped) return;
    this.clearTimers();
    this.setStatus("reconnecting");
    const min = this.options.minBackoffMs ?? 500;
    const max = this.options.maxBackoffMs ?? 15_000;
    const ceiling = Math.min(max, min * 2 ** this.attempt);
    const random = this.options.random ?? Math.random;
    const delay = ceiling / 2 + (ceiling / 2) * random();
    this.attempt += 1;
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null;
      if (!this.stopped) this.connect();
    }, delay);
  }

  private armStaleTimer(): void {
    if (this.staleTimer) clearTimeout(this.staleTimer);
    this.staleTimer = setTimeout(() => {
      this.staleTimer = null;
      const socket = this.socket;
      if (!socket) return;
      this.detach(socket);
      this.socket = null;
      socket.close(4000, "stale");
      this.scheduleRetry();
    }, this.options.staleAfterMs ?? 45_000);
  }

  private detach(socket: WebSocketLike): void {
    socket.onopen = null;
    socket.onmessage = null;
    socket.onclose = null;
    socket.onerror = null;
  }

  private clearTimers(): void {
    if (this.retryTimer) clearTimeout(this.retryTimer);
    if (this.staleTimer) clearTimeout(this.staleTimer);
    this.retryTimer = null;
    this.staleTimer = null;
  }

  private setStatus(status: StreamStatus): void {
    if (status === this.currentStatus) return;
    this.currentStatus = status;
    this.options.onStatus?.(status);
  }
}
