import type { ClientWsEvent, ServerWsEvent } from "../types/generated";

export interface OmiWsClientOptions {
  url: string;
  token?: string;
  baseDelayMs?: number;
  maxDelayMs?: number;
  onEvent: (event: ServerWsEvent) => void;
  onConnectionChange?: (connected: boolean) => void;
  onTtsChunk?: (audioBase64: string, seq: number, isLast: boolean) => void;
  WebSocketImpl?: typeof WebSocket;
}

export class OmiWsClient {
  private url: string;
  private token?: string;
  private baseDelayMs: number;
  private maxDelayMs: number;
  private onEvent: (event: ServerWsEvent) => void;
  private onConnectionChange?: (connected: boolean) => void;
  private onTtsChunk?: (audioBase64: string, seq: number, isLast: boolean) => void;
  private WebSocketImpl: typeof WebSocket;

  private ws: WebSocket | null = null;
  private queue: ClientWsEvent[] = [];
  private attempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private closedManually = false;

  constructor(options: OmiWsClientOptions) {
    this.url = options.url;
    this.token = options.token;
    this.baseDelayMs = options.baseDelayMs ?? 400;
    this.maxDelayMs = options.maxDelayMs ?? 15000;
    this.onEvent = options.onEvent;
    this.onConnectionChange = options.onConnectionChange;
    this.onTtsChunk = options.onTtsChunk;
    this.WebSocketImpl = options.WebSocketImpl ?? WebSocket;
  }

  private buildUrl(): string {
    if (!this.token) return this.url;
    const sep = this.url.includes("?") ? "&" : "?";
    return `${this.url}${sep}token=${encodeURIComponent(this.token)}`;
  }

  connect(): void {
    this.closedManually = false;
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    const socket = new this.WebSocketImpl(this.buildUrl());
    this.ws = socket;

    socket.onopen = () => {
      this.attempt = 0;
      this.onConnectionChange?.(true);
      this.flushQueue();
    };

    socket.onmessage = (msg) => {
      try {
        const parsed = JSON.parse(String(msg.data)) as ServerWsEvent;
        if (parsed.type === "tts.chunk") {
          this.onTtsChunk?.(parsed.payload.audio, parsed.payload.seq, parsed.payload.is_last);
        }
        this.onEvent(parsed);
      } catch {
        // Ignorer une trame malformee sans interrompre la session
      }
    };

    socket.onclose = () => {
      this.ws = null;
      this.onConnectionChange?.(false);
      if (!this.closedManually) {
        this.scheduleReconnect();
      }
    };

    socket.onerror = () => {
      // onclose gere la reconnexion exponentielle
    };
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    const delay = Math.min(this.maxDelayMs, this.baseDelayMs * Math.pow(2, this.attempt));
    this.attempt += 1;
    this.reconnectTimer = setTimeout(() => this.connect(), delay);
  }

  private flushQueue(): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    while (this.queue.length > 0) {
      const next = this.queue.shift();
      if (next) {
        this.ws.send(JSON.stringify(next));
      }
    }
  }

  send(event: ClientWsEvent): boolean {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(event));
      return true;
    }
    this.queue.push(event);
    return false;
  }

  queuedCount(): number {
    return this.queue.length;
  }

  disconnect(): void {
    this.closedManually = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.ws?.close();
    this.ws = null;
    this.onConnectionChange?.(false);
  }
}
