/**
 * WebSocket Connection Manager
 * 
 * Handles WebSocket connections with auto-reconnect, heartbeat,
 * and event-based message handling.
 */

import {
  WS_URL,
  WS_RECONNECT_INTERVAL,
  WS_MAX_RECONNECT_ATTEMPTS,
  WS_HEARTBEAT_INTERVAL,
} from "@/lib/constants";

export type MessageHandler = (data: unknown) => void;
export type ConnectionState = "connecting" | "connected" | "disconnected" | "reconnecting";

export interface WebSocketOptions {
  url: string;
  onMessage?: MessageHandler;
  onStateChange?: (state: ConnectionState) => void;
  reconnectInterval?: number;
  maxReconnectAttempts?: number;
  heartbeatInterval?: number;
}

export class WebSocketConnection {
  private ws: WebSocket | null = null;
  private url: string;
  private onMessage?: MessageHandler;
  private onStateChange?: (state: ConnectionState) => void;
  private reconnectInterval: number;
  private maxReconnectAttempts: number;
  private heartbeatInterval: number;
  private reconnectAttempts = 0;
  private reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimeout: ReturnType<typeof setInterval> | null = null;
  private state: ConnectionState = "disconnected";
  private manualClose = false;

  constructor(options: WebSocketOptions) {
    this.url = options.url;
    this.onMessage = options.onMessage;
    this.onStateChange = options.onStateChange;
    this.reconnectInterval = options.reconnectInterval || WS_RECONNECT_INTERVAL;
    this.maxReconnectAttempts = options.maxReconnectAttempts || WS_MAX_RECONNECT_ATTEMPTS;
    this.heartbeatInterval = options.heartbeatInterval || WS_HEARTBEAT_INTERVAL;
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
          // Handle pong responses
          if (event.data === "pong") {
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

        // Don't reconnect if manually closed or max attempts reached
        // Also stop if we're getting resource errors (code 1006 with no clean close)
        const shouldReconnect = !this.manualClose &&
          this.reconnectAttempts < this.maxReconnectAttempts &&
          event.code !== 1008 && // Policy violation
          event.code !== 1011;   // Server error

        if (shouldReconnect) {
          this.setState("reconnecting");
          this.scheduleReconnect();
        } else {
          this.setState("disconnected");
        }
      };

      this.ws.onerror = () => {
        // WebSocket errors are expected when backend is offline
        // Don't log - the onclose handler will manage reconnection
        // Increment attempts on error to prevent infinite loops
        this.reconnectAttempts++;
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

  private startHeartbeat(): void {
    this.stopHeartbeat();
    this.heartbeatTimeout = setInterval(() => {
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
    
    const delay = this.reconnectInterval * Math.pow(1.5, this.reconnectAttempts);
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
