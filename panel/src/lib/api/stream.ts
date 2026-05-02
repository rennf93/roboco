/**
 * Stream API Client
 *
 * API functions for transcription and extraction operations.
 */

import api from "./client";
import { isMockMode } from "@/lib/mock-data";

// =============================================================================
// Types
// =============================================================================

export interface StreamChunkRequest {
  channel_name: string;
  audio_data: string; // Base64 encoded
  chunk_index: number;
  is_final?: boolean;
}

export interface StreamChunkResponse {
  status: string;
  chunk_index: number;
  queued: boolean;
}

export interface StreamCompleteRequest {
  channel_name: string;
  session_id?: string;
}

export interface StreamCompleteResponse {
  status: string;
  channel_name: string;
  total_chunks: number;
  transcription_id?: string;
}

export interface ExtractionRequest {
  content: string;
  extraction_type: "tasks" | "decisions" | "action_items" | "summary";
  context?: string;
}

export interface ExtractionResponse {
  extraction_type: string;
  results: Record<string, unknown>[];
  confidence: number;
}

export interface TranscriptionStats {
  total_transcriptions: number;
  active_channels: number;
  chunks_processed: number;
  avg_processing_time_ms: number;
}

export interface ChannelPermissions {
  channel_name: string;
  can_transcribe: boolean;
  can_extract: boolean;
  rate_limit?: number;
}

// =============================================================================
// API Client
// =============================================================================

export const streamApi = {
  // ===========================================================================
  // TRANSCRIPTION ENDPOINTS
  // ===========================================================================

  /**
   * Submit an audio chunk for transcription
   */
  submitChunk: async (request: StreamChunkRequest): Promise<StreamChunkResponse> => {
    if (isMockMode()) {
      return {
        status: "queued",
        chunk_index: request.chunk_index,
        queued: true,
      };
    }
    const { data } = await api.post<StreamChunkResponse>("/stream/chunk", request);
    return data;
  },

  /**
   * Complete a transcription session
   */
  complete: async (request: StreamCompleteRequest): Promise<StreamCompleteResponse> => {
    if (isMockMode()) {
      return {
        status: "completed",
        channel_name: request.channel_name,
        total_chunks: 10,
        transcription_id: `trans-${Date.now()}`,
      };
    }
    const { data } = await api.post<StreamCompleteResponse>("/stream/complete", request);
    return data;
  },

  // ===========================================================================
  // EXTRACTION ENDPOINTS
  // ===========================================================================

  /**
   * Extract structured information from content
   */
  extract: async (request: ExtractionRequest): Promise<ExtractionResponse> => {
    if (isMockMode()) {
      return {
        extraction_type: request.extraction_type,
        results: [],
        confidence: 0.85,
      };
    }
    const { data } = await api.post<ExtractionResponse>("/stream/extract", request);
    return data;
  },

  // ===========================================================================
  // STATS & PERMISSIONS
  // ===========================================================================

  /**
   * Get transcription statistics
   */
  getStats: async (): Promise<TranscriptionStats> => {
    if (isMockMode()) {
      return {
        total_transcriptions: 100,
        active_channels: 5,
        chunks_processed: 1500,
        avg_processing_time_ms: 250,
      };
    }
    const { data } = await api.get<TranscriptionStats>("/stream/stats");
    return data;
  },

  /**
   * Get all channel permissions
   */
  getPermissions: async (): Promise<ChannelPermissions[]> => {
    if (isMockMode()) {
      return [];
    }
    const { data } = await api.get<ChannelPermissions[]>("/stream/permissions");
    return data;
  },

  /**
   * Get permissions for a specific channel
   */
  getChannelPermissions: async (channelName: string): Promise<ChannelPermissions> => {
    if (isMockMode()) {
      return {
        channel_name: channelName,
        can_transcribe: true,
        can_extract: true,
      };
    }
    const { data } = await api.get<ChannelPermissions>(`/stream/permissions/channel/${channelName}`);
    return data;
  },
};
