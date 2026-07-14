import api from "./client";

// ---------------------------------------------------------------------------
// Telegram notifications bridge — the CEO's bot-token + chat-id credentials
// (write-only; the API never returns the stored secrets). The fan-out itself
// runs server-side from the CEO-notify producers; this surface is the
// credentials card only.
// ---------------------------------------------------------------------------

export interface TelegramCredentialsStatus {
  has_credentials: boolean;
}

export const telegramApi = {
  getCredentialsStatus: async (): Promise<TelegramCredentialsStatus> => {
    const { data } =
      await api.get<TelegramCredentialsStatus>("/telegram/credentials");
    return data;
  },
  setCredentials: async (creds: {
    bot_token: string;
    chat_id: string;
  }): Promise<TelegramCredentialsStatus> => {
    const { data } = await api.post<TelegramCredentialsStatus>(
      "/telegram/credentials",
      creds,
    );
    return data;
  },
};