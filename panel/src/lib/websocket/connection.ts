/**
 * WebSocket Connection Manager
 *
 * Handles WebSocket connections with auto-reconnect, heartbeat,
 * and event-based message handling.
 */

import {
  WS_URL,
  WS_RECONNECT_INTERVAL,
  WS_RECONNECT_MAX_INTERVAL,
  WS_HEARTBEAT_INTERVAL,
  WS_PONG_TIMEOUT_MS,
} from "@/lib/constants";

export type MessageHandler = (data: unknown) => void;
export type ConnectionState =
  | "connecting"
  | "connected"
  | "disconnected"
  | "reconnecting";

export interface WebSocketOptions {
  url: string;
  onMessage?: MessageHandler;
  onStateChange?: (state: ConnectionState) => void;
  reconnectInterval?: number;
  heartbeatInterval?: number;
  pongTimeout?: number;
}

export class WebSocketConnection {
  private ws: WebSocket | null = null;
  private url: string;
  private onMessage?: MessageHandler;
  private onStateChange?: (state: ConnectionState) => void;
  private reconnectInterval: number;
  private heartbeatInterval: number;
  private pongTimeout: number;
  private reconnectAttempts = 0;
  private reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimeout: ReturnType<typeof setInterval> | null = null;
  private state: ConnectionState = "disconnected";
  private manualClose = false;
  private lastPongAt = 0;

  constructor(options: WebSocketOptions) {
    this.url = options.url;
    this.onMessage = options.onMessage;
    this.onStateChange = options.onStateChange;
    this.reconnectInterval = options.reconnectInterval || WS_RECONNECT_INTERVAL;
    this.heartbeatInterval = options.heartbeatInterval || WS_HEARTBEAT_INTERVAL;
    this.pongTimeout = options.pongTimeout || WS_PONG_TIMEOUT_MS;
  }

  private setState(state: ConnectionState): void {
    this.state = state;
    this.onStateChange?.(state);
  }

  connect(): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      return;
    }

    this.manualClose = false;
    this.lastPongAt = Date.now();
    this.setState("connecting");

    try {
      this.ws = new WebSocket(this.url);

      this.ws.onopen = () => {
        this.setState("connected");
        this.reconnectAttempts = 0;
        this.startHeartbeat();
      };

      this.ws.onmessage = (event) => {
        try {
          // pong frames refresh the watchdog; data frames dispatch to onMessage
          if (event.data === "pong") {
            this.lastPongAt = Date.now();
            return;
          }

          const data = JSON.parse(event.data);
          this.onMessage?.(data);
        } catch {
          // Silently ignore parse errors
        }
      };

      this.ws.onclose = (event) => {
        this.stopHeartbeat();

        // Reconnect forever unless the close was manual or a hard server-side
        // error (policy violation / server crash) — those won't recover by retry.
        const shouldReconnect =
          !this.manualClose &&
          event.code !== 1008 && // Policy violation
          event.code !== 1011; // Server error

        if (shouldReconnect) {
          this.setState("reconnecting");
          this.scheduleReconnect();
        } else {
          this.setState("disconnected");
        }
      };

      this.ws.onerror = () => {
        // WebSocket errors are expected when backend is offline; onclose
        // manages reconnection. Do NOT advance reconnectAttempts here —
        // scheduleReconnect() is the sole place that advances it.
      };
    } catch {
      // Connection failed - backend likely offline
      this.setState("disconnected");
    }
  }

  disconnect(): void {
    this.manualClose = true;
    this.stopHeartbeat();
    this.clearReconnectTimeout();

    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }

    this.setState("disconnected");
  }

  send(data: string | object): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      const message = typeof data === "string" ? data : JSON.stringify(data);
      this.ws.send(message);
    }
  }

  getState(): ConnectionState {
    return this.state;
  }

  getLastPongAt(): number {
    return this.lastPongAt;
  }

  // Watchdog: force-close the socket if no pong has arrived within the timeout
  // window. The onclose handler then routes through the normal reconnect path.
  // Exposed publicly so the heartbeat tick (and tests) can call it directly.
  checkPong(): void {
    if (this.ws && Date.now() - this.lastPongAt >= this.pongTimeout) {
      this.ws.close();
    }
  }

  private startHeartbeat(): void {
    this.stopHeartbeat();
    this.heartbeatTimeout = setInterval(() => {
      // Watchdog first: if the server stopped responding to pings, force-close
      // so onclose fires the reconnect path instead of pinging into the void.
      this.checkPong();
      this.send("ping");
    }, this.heartbeatInterval);
  }

  private stopHeartbeat(): void {
    if (this.heartbeatTimeout) {
      clearInterval(this.heartbeatTimeout);
      this.heartbeatTimeout = null;
    }
  }

  private scheduleReconnect(): void {
    this.clearReconnectTimeout();

    // Cap the exponent so Math.pow doesn't overflow once attempts grows large:
    // once the uncapped delay exceeds the cap, the capped delay is just the cap,
    // so further exponentiation changes nothing — pin attempts at the floor.
    const raw = this.reconnectInterval * Math.pow(1.5, this.reconnectAttempts);
    // ponytail: delay capped at WS_RECONNECT_MAX_INTERVAL; counter grows but delay is bounded.
    const delay = Math.min(raw, WS_RECONNECT_MAX_INTERVAL);
    this.reconnectAttempts++;

    this.reconnectTimeout = setTimeout(() => {
      this.connect();
    }, delay);
  }

  private clearReconnectTimeout(): void {
    if (this.reconnectTimeout) {
      clearTimeout(this.reconnectTimeout);
      this.reconnectTimeout = null;
    }
  }
}

/**
 * Get WebSocket base URL from environment or construct from current location
 */
export function getWebSocketUrl(): string {
  // If we have an absolute WS URL configured, use it
  if (WS_URL.startsWith("ws://") || WS_URL.startsWith("wss://")) {
    return WS_URL;
  }

  // For relative URLs, construct absolute WebSocket URL from current location
  if (typeof window !== "undefined") {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;
    const path = WS_URL.startsWith("/") ? WS_URL : `/${WS_URL}`;
    return `${protocol}//${host}${path}`;
  }

  // Fallback for SSR
  return WS_URL;
}
