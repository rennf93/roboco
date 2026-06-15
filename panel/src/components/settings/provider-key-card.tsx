"use client";

import { useState } from "react";
import { Key, Eye, EyeOff, Check, X, Loader2 } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { useAllProviderKeys, useSetProviderKey } from "@/hooks/use-providers";

/**
 * ProviderKeyCard — API key management for all AI providers.
 *
 * Shows each seeded provider (Anthropic, Ollama Cloud, OpenAI, Self-Hosted)
 * with its key status and an input to set or clear the key.
 *
 * This replaces the Docker-mounted ~/.claude authentication model with
 * a UI-driven key management flow — enter keys once, store encrypted,
 * use from any provider (Docker or not).
 */
export function ProviderKeyCard() {
  const { data: keys, isLoading, isError } = useAllProviderKeys();
  const setKey = useSetProviderKey();

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Key className="h-5 w-5" />
            API Keys
          </CardTitle>
          <CardDescription>Loading provider key status...</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        </CardContent>
      </Card>
    );
  }

  if (isError) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Key className="h-5 w-5" />
            API Keys
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-destructive">
            Failed to load provider key status. Make sure the orchestrator is running.
          </p>
        </CardContent>
      </Card>
    );
  }

  if (!keys || keys.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Key className="h-5 w-5" />
            API Keys
          </CardTitle>
          <CardDescription>
            No providers found. Run database migrations first.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Key className="h-5 w-5" />
          API Keys
        </CardTitle>
        <CardDescription>
          Manage API keys for each AI provider. Keys are encrypted at rest
          and never exposed to the frontend. Set a key here instead of relying
          on Docker-mounted credentials.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {keys.map((provider) => (
          <ProviderKeyRow
            key={provider.provider_type}
            {...provider}
            onSave={(apiKey) =>
              setKey.mutate({
                providerType: provider.provider_type,
                apiKey,
              })
            }
            isSaving={
              setKey.isPending &&
              setKey.variables?.providerType === provider.provider_type
            }
          />
        ))}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Single provider key row
// ---------------------------------------------------------------------------

interface ProviderKeyRowProps {
  provider_type: string;
  display_name: string;
  has_key: boolean;
  enabled: boolean;
  onSave: (apiKey: string) => void;
  isSaving: boolean;
}

function ProviderKeyRow({
  provider_type,
  display_name,
  has_key,
  enabled,
  onSave,
  isSaving,
}: ProviderKeyRowProps) {
  const [value, setValue] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [saved, setSaved] = useState(false);

  const handleSave = () => {
    onSave(value);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  const handleClear = () => {
    setValue("");
    onSave("");
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  const showClearButton = has_key && !value;

  return (
    <div className="rounded-lg border p-4 space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="font-medium">{display_name}</span>
          <Badge
            variant={enabled && has_key ? "default" : "secondary"}
            className="text-xs"
          >
            {enabled && has_key ? "active" : has_key ? "disabled" : "not set"}
          </Badge>
        </div>
        {saved && (
          <span className="flex items-center gap-1 text-xs text-emerald-500">
            <Check className="h-3 w-3" />
            Saved
          </span>
        )}
      </div>

      {/* Input row */}
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Input
            type={showKey ? "text" : "password"}
            placeholder={
              has_key
                ? "••••••••••••••••••••"
                : `Enter ${display_name} API key`
            }
            value={value}
            onChange={(e) => setValue(e.target.value)}
            className="pr-10"
          />
          {/* Toggle visibility */}
          <button
            type="button"
            onClick={() => setShowKey(!showKey)}
            className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
            tabIndex={-1}
          >
            {showKey ? (
              <EyeOff className="h-4 w-4" />
            ) : (
              <Eye className="h-4 w-4" />
            )}
          </button>
        </div>

        <Button
          size="sm"
          onClick={handleSave}
          disabled={!value || isSaving}
        >
          {isSaving ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            "Save"
          )}
        </Button>

        {showClearButton && (
          <Button
            size="sm"
            variant="outline"
            onClick={handleClear}
            disabled={isSaving}
            className="text-destructive"
          >
            <X className="h-4 w-4 mr-1" />
            Clear
          </Button>
        )}
      </div>

      {/* Hint */}
      <p className="text-xs text-muted-foreground">
        {provider_type === "anthropic" &&
          "Set your Anthropic API key to authenticate agents without Docker."}
        {provider_type === "ollama_cloud" &&
          "Your Ollama Cloud subscription token for routed agents."}
        {provider_type === "openai" &&
          "Reserved for OpenAI provider integration."}
        {provider_type === "local" &&
          "Self-hosted Ollama server — configure via the Self-Hosted section below."}
      </p>
    </div>
  );
}
