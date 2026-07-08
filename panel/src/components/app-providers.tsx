"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ReactQueryDevtools } from "@tanstack/react-query-devtools";
import { ThemeProvider } from "next-themes";
import { useState } from "react";
import { Toaster } from "@/components/ui/sonner";
import { PageRefreshProvider } from "@/components/providers";
import { useAgentRosterSync } from "@/hooks/use-agents";

// Keeps the agent display-name resolver (agent-utils) in sync with the live
// `/api/agents` roster. Must live inside QueryClientProvider. Renders nothing.
function AgentRosterSync() {
  useAgentRosterSync();
  return null;
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // Keep data fresh for 5 minutes - reduces refetches on navigation
            staleTime: 5 * 60 * 1000,
            // Keep unused data in cache for 30 minutes
            gcTime: 30 * 60 * 1000,
            // Intentionally disabled: refetching on window focus causes excessive
            // API calls when the user alt-tabs back to the panel.
            refetchOnWindowFocus: false,
            // Don't refetch on reconnect by default
            refetchOnReconnect: false,
            retry: 1,
          },
        },
      }),
  );

  return (
    <PageRefreshProvider>
      <ThemeProvider
        attribute="class"
        defaultTheme="system"
        enableSystem
        disableTransitionOnChange
      >
        <QueryClientProvider client={queryClient}>
          <AgentRosterSync />
          {children}
          <Toaster position="top-right" />
          {process.env.NODE_ENV === "development" && (
            <ReactQueryDevtools initialIsOpen={false} />
          )}
        </QueryClientProvider>
      </ThemeProvider>
    </PageRefreshProvider>
  );
}
