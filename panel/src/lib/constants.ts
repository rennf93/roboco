/**
 * Application Constants
 * 
 * Centralized configuration values used across the application.
 */

// CEO agent credentials for control panel operations
// In production, this would come from authentication
export const CEO_AGENT_ID = "00000000-0000-0000-0000-000000000001";
export const CEO_ROLE = "ceo";

// API URLs - relative URLs go through Next.js proxy (avoids CORS)
export const API_URL = process.env.NEXT_PUBLIC_API_URL || "/api";
export const WS_URL = process.env.NEXT_PUBLIC_WS_URL || "/ws";

// Pagination defaults
export const DEFAULT_PAGE_SIZE = 20;
export const MAX_PAGE_SIZE = 100;

// WebSocket settings
export const WS_RECONNECT_INTERVAL = 5000;  // Start at 5s, exponential backoff from there
export const WS_MAX_RECONNECT_ATTEMPTS = 3; // Give up after 3 attempts
export const WS_HEARTBEAT_INTERVAL = 30000;

// UI settings
export const STREAM_MAX_MESSAGES = 100;
export const NOTIFICATION_MAX_DISPLAY = 10;
