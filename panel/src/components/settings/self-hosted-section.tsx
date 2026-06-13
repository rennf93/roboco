"use client";

import { useState, useCallback } from "react";
import {
  useSelfHostedConfig,
  useSetSelfHostedConfig,
  useTestSelfHosted,
  useSelfHostedModels,
  useRefreshSelfHostedModels,
} from "@/hooks/use-providers";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  AlertTriangle,
  CheckCircle2,
  Eye,
  EyeOff,
  RefreshCw,
  Server,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";
import type { SelfHostedTestResult } from "@/lib/api/providers";

// Relative-time helper (no date-fns dependency).
function relativeTime(isoDate: string | null): string {
  if (!isoDate) return "never";
  const diff = Date.now() - new Date(isoDate).getTime();
  const s = Math.floor(diff / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : "Unknown error";
}

/** Props exposed to the parent (ai-routing-card) so it can read test status. */
export interface SelfHostedSectionProps {
  /** Called whenever a successful connection test completes. */
  onTestSuccess?: (modelCount: number) => void;
  /** The last known test result — driven by parent if the parent stores it. */
  testResult?: SelfHostedTestResult | null;
  /** Called when the user clicks "Test Connection" and we get any result. */
  onTestResult?: (result: SelfHostedTestResult) => void;
}

export function SelfHostedSection({
  onTestSuccess,
  testResult,
  onTestResult,
}: SelfHostedSectionProps) {
  const { data: config } = useSelfHostedConfig();
  const { data: models = [] } = useSelfHostedModels();

  const saveConfig = useSetSelfHostedConfig();
  const testConnection = useTestSelfHosted();
  const refreshModels = useRefreshSelfHostedModels();

  // Local form state
  const [baseUrl, setBaseUrl] = useState("");
  const [authToken, setAuthToken] = useState("");
  const [showToken, setShowToken] = useState(false);

  // Timestamps for "Last refreshed" label
  const [lastRefreshed, setLastRefreshed] = useState<string | null>(null);

  const hasSavedUrl = !!(config?.base_url);

  // ---- Save handler --------------------------------------------------------
  const handleSave = async () => {
    const url = baseUrl.trim();
    if (!url) {
      toast.error("Enter a base URL first");
      return;
    }
    try {
      await saveConfig.mutateAsync({
        base_url: url,
        ...(authToken ? { auth_token: authToken } : {}),
      });
      toast.success("Self-hosted config saved");
      setBaseUrl("");
      setAuthToken("");
    } catch (e) {
      toast.error("Save failed: " + errMsg(e));
    }
  };

  // ---- Test Connection handler ---------------------------------------------
  const handleTest = async () => {
    try {
      const result = await testConnection.mutateAsync();
      onTestResult?.(result);
      if (result.ok) {
        onTestSuccess?.(result.model_count ?? 0);
        setLastRefreshed(new Date().toISOString());
        const count = result.model_count ?? 0;
        toast.success(
          `Connected — ${count} model${count === 1 ? "" : "s"} available`,
        );
      } else {
        toast.error(`Connection failed: ${result.error ?? "unknown error"}`);
      }
    } catch (e) {
      toast.error("Test failed: " + errMsg(e));
    }
  };

  // ---- Refresh Models handler ----------------------------------------------
  const handleRefresh = useCallback(async () => {
    try {
      await refreshModels.mutateAsync();
      setLastRefreshed(new Date().toISOString());
      toast.success("Model list refreshed");
    } catch (e) {
      toast.error("Refresh failed: " + errMsg(e));
    }
  }, [refreshModels]);

  // ---- Retry handler (from error empty state) ------------------------------
  const handleRetry = () => handleTest();

  // Determine which empty state to show, if any.
  const showNoUrlState = !hasSavedUrl;
  const showErrorState =
    hasSavedUrl && testResult?.ok === false && !showNoUrlState;
  const showNoModelsState =
    hasSavedUrl &&
    testResult?.ok === true &&
    models.length === 0 &&
    !showNoUrlState;
  const showModelList =
    hasSavedUrl && testResult?.ok === true && models.length > 0;

  return (
    <section className="space-y-4">
      {/* Header */}
      <div className="flex items-center gap-2">
        <Server className="h-4 w-4 text-muted-foreground" />
        <Label className="text-sm font-medium">Self-Hosted LLM</Label>
        {testResult?.ok === true && (
          <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-xs font-medium text-emerald-600">
            <CheckCircle2 className="h-3 w-3" /> connected
          </span>
        )}
        {testResult?.ok === false && (
          <span className="inline-flex items-center gap-1 rounded-full bg-red-500/10 px-2 py-0.5 text-xs font-medium text-red-600">
            <XCircle className="h-3 w-3" /> error
          </span>
        )}
      </div>

      {/* Base URL input */}
      <div className="space-y-1">
        <Label className="text-xs text-muted-foreground">Base URL</Label>
        <div className="flex gap-2">
          <Input
            type="text"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder={
              hasSavedUrl
                ? config.base_url ?? "http://localhost:11434"
                : "http://localhost:11434"
            }
            className="font-mono text-sm"
          />
        </div>
      </div>

      {/* Auth token input with Eye toggle */}
      <div className="space-y-1">
        <Label className="text-xs text-muted-foreground">
          Auth token{" "}
          <span className="text-muted-foreground/60">(optional)</span>
        </Label>
        <div className="flex gap-2">
          <div className="relative flex-1">
            <Input
              type={showToken ? "text" : "password"}
              value={authToken}
              onChange={(e) => setAuthToken(e.target.value)}
              placeholder={
                config?.has_token
                  ? "•••••••••••• (leave blank to keep)"
                  : "sk-… or Bearer token"
              }
              className="pr-10"
            />
            <button
              type="button"
              onClick={() => setShowToken((v) => !v)}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              aria-label={showToken ? "Hide token" : "Show token"}
            >
              {showToken ? (
                <EyeOff className="h-4 w-4" />
              ) : (
                <Eye className="h-4 w-4" />
              )}
            </button>
          </div>
          <Button onClick={handleSave} disabled={saveConfig.isPending}>
            {saveConfig.isPending ? "Saving…" : "Save"}
          </Button>
        </div>
        {config?.has_token && (
          <p className="text-xs text-muted-foreground">
            A token is stored. Type a new value to update it.
          </p>
        )}
        {!config?.has_token && (
          <p className="text-xs text-muted-foreground">
            Stored Fernet-encrypted server-side; never returned by the API.
          </p>
        )}
      </div>

      {/* Test Connection button + inline result badge */}
      <div className="flex items-center gap-3">
        <Button
          variant="outline"
          size="sm"
          onClick={handleTest}
          disabled={!hasSavedUrl || testConnection.isPending}
        >
          {testConnection.isPending ? (
            <>
              <RefreshCw className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              Testing…
            </>
          ) : (
            "Test Connection"
          )}
        </Button>

        {/* Inline result badge */}
        {testResult?.ok === true && (
          <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-3 py-1 text-xs font-medium text-emerald-700 dark:text-emerald-400">
            <CheckCircle2 className="h-3.5 w-3.5" />
            Connected &mdash; {testResult.model_count ?? 0} model
            {(testResult.model_count ?? 0) === 1 ? "" : "s"} available
          </span>
        )}
        {testResult?.ok === false && (
          <span className="inline-flex items-center gap-1 rounded-full bg-red-500/10 px-3 py-1 text-xs font-medium text-red-700 dark:text-red-400">
            <XCircle className="h-3.5 w-3.5" />
            {testResult.error ?? "Connection failed"}
          </span>
        )}
      </div>

      {/* ── Empty state 1: no base URL configured ── */}
      {showNoUrlState && (
        <div className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
          <p className="font-medium">No base URL configured</p>
          <p className="mt-1 text-xs">
            Enter the URL of your self-hosted LLM endpoint (e.g.{" "}
            <code>http://localhost:11434</code> for Ollama) and click{" "}
            <strong>Save</strong>. Then click <strong>Test Connection</strong>{" "}
            to verify and discover available models.
          </p>
        </div>
      )}

      {/* ── Empty state 2: error state ── */}
      {showErrorState && (
        <div className="rounded-md border border-red-200 bg-red-50 p-4 dark:border-red-900/40 dark:bg-red-950/20">
          <div className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-red-600" />
            <div className="flex-1 space-y-1 text-sm">
              <p className="font-medium text-red-700 dark:text-red-400">
                Last test failed
              </p>
              <p className="text-xs text-red-600 dark:text-red-500">
                {testResult?.error ?? "Unknown error"}
              </p>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={handleRetry}
              disabled={testConnection.isPending}
            >
              Retry
            </Button>
          </div>
        </div>
      )}

      {/* ── Empty state 3: connected but 0 models ── */}
      {showNoModelsState && (
        <div className="rounded-md border border-amber-200 bg-amber-50 p-4 dark:border-amber-900/40 dark:bg-amber-950/20">
          <div className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
            <p className="text-sm text-amber-700 dark:text-amber-400">
              Connected but no models found. Pull a model first (e.g.{" "}
              <code className="font-mono">ollama pull llama3.2</code>) then
              click <strong>Refresh Models</strong>.
            </p>
          </div>
        </div>
      )}

      {/* ── Model list ── */}
      {showModelList && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <p className="text-xs text-muted-foreground">
              Last refreshed:{" "}
              <span className="font-medium">
                {relativeTime(lastRefreshed)}
              </span>
            </p>
            <Button
              variant="ghost"
              size="sm"
              onClick={handleRefresh}
              disabled={refreshModels.isPending}
            >
              {refreshModels.isPending ? (
                <>
                  <RefreshCw className="mr-1.5 h-3 w-3 animate-spin" />
                  Refreshing…
                </>
              ) : (
                <>
                  <RefreshCw className="mr-1.5 h-3 w-3" />
                  Refresh Models
                </>
              )}
            </Button>
          </div>
          <div className="divide-y rounded-md border">
            {models.map((m) => (
              <div
                key={m.model_name}
                className="flex items-center justify-between px-3 py-2"
              >
                <div>
                  <span className="text-sm font-medium">{m.display_name}</span>
                  <span className="ml-2 font-mono text-xs text-muted-foreground">
                    {m.model_name}
                  </span>
                </div>
                <Badge variant="secondary" className="text-xs">
                  auto-discovered
                </Badge>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
