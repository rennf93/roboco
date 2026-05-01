import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  providersApi,
  type ApplyModePayload,
} from "@/lib/api/providers";

export const providerKeys = {
  all: ["providers"] as const,
  catalog: () => [...providerKeys.all, "catalog"] as const,
  ollamaKey: () => [...providerKeys.all, "ollama-key"] as const,
  mode: () => [...providerKeys.all, "mode"] as const,
};

export function useCatalog() {
  return useQuery({
    queryKey: providerKeys.catalog(),
    queryFn: () => providersApi.catalog(),
    staleTime: 5 * 60_000, // 5 minutes — static list
  });
}

export function useOllamaKey() {
  return useQuery({
    queryKey: providerKeys.ollamaKey(),
    queryFn: () => providersApi.getOllamaKey(),
    staleTime: 60_000,
  });
}

export function useSetOllamaKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (apiKey: string) => providersApi.setOllamaKey(apiKey),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: providerKeys.ollamaKey() });
      // Applying a mode also reads this so refresh it too.
      qc.invalidateQueries({ queryKey: providerKeys.mode() });
    },
  });
}

export function useRoutingMode() {
  return useQuery({
    queryKey: providerKeys.mode(),
    queryFn: () => providersApi.getMode(),
    staleTime: 30_000,
  });
}

export function useApplyMode() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: ApplyModePayload) => providersApi.applyMode(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: providerKeys.mode() });
    },
  });
}
