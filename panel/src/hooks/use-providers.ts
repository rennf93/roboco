import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  providersApi,
  type ApplyModePayload,
  type ComplexityLevel,
  type ComplexityOverride,
  type SelfHostedConfigPayload,
} from "@/lib/api/providers";

export const providerKeys = {
  all: ["providers"] as const,
  catalog: () => [...providerKeys.all, "catalog"] as const,
  ollamaKey: () => [...providerKeys.all, "ollama-key"] as const,
  grokKey: () => [...providerKeys.all, "grok-key"] as const,
  mode: () => [...providerKeys.all, "mode"] as const,
  selfHostedConfig: () => [...providerKeys.all, "self-hosted-config"] as const,
  selfHostedModels: () => [...providerKeys.all, "self-hosted-models"] as const,
  selfHostedTest: () => [...providerKeys.all, "self-hosted-test"] as const,
  complexityOverrides: () =>
    [...providerKeys.all, "complexity-overrides"] as const,
  presets: () => [...providerKeys.all, "presets"] as const,
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

export function useGrokKey() {
  return useQuery({
    queryKey: providerKeys.grokKey(),
    queryFn: () => providersApi.getGrokKey(),
    staleTime: 60_000,
  });
}

export function useSetGrokKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (apiKey: string) => providersApi.setGrokKey(apiKey),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: providerKeys.grokKey() });
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
      // Complexity overrides survive a mode switch (see the backend's
      // _wipe_mode_switch_assignments carve-out) but the mode-apply response
      // doesn't carry them — refresh separately so the card doesn't show a
      // stale list. Mirrors useApplyPreset, which already invalidates both.
      qc.invalidateQueries({ queryKey: providerKeys.complexityOverrides() });
    },
  });
}

// ---------------------------------------------------------------------------
// Self-hosted LLM hooks
// ---------------------------------------------------------------------------

/** Query: fetch saved self-hosted config (base_url + has_token flag). */
export function useSelfHostedConfig() {
  return useQuery({
    queryKey: providerKeys.selfHostedConfig(),
    queryFn: () => providersApi.getSelfHostedConfig(),
    staleTime: 30_000,
  });
}

/** Mutation: save self-hosted base URL + optional auth token. */
export function useSetSelfHostedConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: SelfHostedConfigPayload) =>
      providersApi.saveSelfHostedConfig(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: providerKeys.selfHostedConfig() });
    },
  });
}

/** Mutation: test the self-hosted connection and return status + model count. */
export function useTestSelfHosted() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => providersApi.testSelfHosted(),
    onSuccess: () => {
      // After a successful test, also refresh the model list.
      qc.invalidateQueries({ queryKey: providerKeys.selfHostedModels() });
    },
  });
}

/** Query: list models discovered from the self-hosted endpoint. */
export function useSelfHostedModels() {
  return useQuery({
    queryKey: providerKeys.selfHostedModels(),
    queryFn: () => providersApi.getSelfHostedModels(),
    staleTime: 2 * 60_000, // 2 minutes
  });
}

/** Mutation: invalidate the cached model list so it is re-fetched from GET /self-hosted/models. */
export function useRefreshSelfHostedModels() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      await qc.invalidateQueries({ queryKey: providerKeys.selfHostedModels() });
    },
  });
}

// ---------------------------------------------------------------------------
// Complexity overrides
// ---------------------------------------------------------------------------

export function useComplexityOverrides() {
  return useQuery({
    queryKey: providerKeys.complexityOverrides(),
    queryFn: () => providersApi.getComplexityOverrides(),
    staleTime: 30_000,
  });
}

export function useSetComplexityOverride() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: ComplexityOverride) =>
      providersApi.setComplexityOverride(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: providerKeys.complexityOverrides() });
    },
  });
}

export function useDeleteComplexityOverride() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      role,
      complexity,
    }: {
      role: string;
      complexity: ComplexityLevel;
    }) => providersApi.deleteComplexityOverride(role, complexity),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: providerKeys.complexityOverrides() });
    },
  });
}

// ---------------------------------------------------------------------------
// Routing presets
// ---------------------------------------------------------------------------

export function useRoutingPresets() {
  return useQuery({
    queryKey: providerKeys.presets(),
    queryFn: () => providersApi.listPresets(),
    staleTime: 30_000,
  });
}

export function useSavePreset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => providersApi.savePreset(name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: providerKeys.presets() });
    },
  });
}

export function useApplyPreset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => providersApi.applyPreset(id),
    onSuccess: () => {
      // A preset fully replaces the routing state — refresh everything the
      // card renders off of, not just the mode snapshot.
      qc.invalidateQueries({ queryKey: providerKeys.mode() });
      qc.invalidateQueries({ queryKey: providerKeys.complexityOverrides() });
    },
  });
}

export function useDeletePreset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => providersApi.deletePreset(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: providerKeys.presets() });
    },
  });
}
