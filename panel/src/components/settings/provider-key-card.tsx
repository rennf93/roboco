"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { HelpTip } from "@/components/ui/help-tip";
import { Check, Key, KeyRound } from "lucide-react";
import { toast } from "sonner";

// API contract type — mirrors fe-dev-1's OpenRouterKeyStatus (parallel work).
interface OpenRouterKeyStatus {
  key_set: boolean;
}

// Shared query key so react-query deduplicates across the ProviderKeyRow and
// the AIRoutingCard's own key-status read for ModeButton gating.
export const openRouterKeyQueryKey = ["providers", "openrouter-key"] as const;

/** Query: fetch the OpenRouter key status (key_set boolean). */
export function useOpenRouterKeyStatus() {
  return useQuery({
    queryKey: openRouterKeyQueryKey,
    queryFn: async () => {
      const { data } = await api.get<OpenRouterKeyStatus>(
        "/providers/openrouter-key",
      );
      return data;
    },
    staleTime: 60_000,
  });
}

/** Mutation: save or clear (empty string) the OpenRouter API key. */
function useSetOpenRouterKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (apiKey: string) => {
      const { data } = await api.put<OpenRouterKeyStatus>(
        "/providers/openrouter-key",
        { api_key: apiKey },
      );
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: openRouterKeyQueryKey });
      qc.invalidateQueries({ queryKey: ["providers", "mode"] });
    },
  });
}

/**
 * OpenRouter key row — password input, Save and Clear buttons.
 *
 * False-Saved bug fix (PR #170 review issue #5): the "Saved" badge appears
 * ONLY after the mutation succeeds (onSuccess), never optimistically. The
 * badge clears on any input change so a stale "Saved" can't linger after the
 * operator edits the field.
 */
export function OpenRouterProviderKeyRow() {
  const { data: keyStatus } = useOpenRouterKeyStatus();
  const setKeyMut = useSetOpenRouterKey();

  const hasKey = !!keyStatus?.key_set;
  const [apiKey, setApiKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [saved, setSaved] = useState(false);

  const handleSave = async () => {
    setSaved(false);
    try {
      if (clearKey) {
        await setKeyMut.mutateAsync("");
        toast.success("OpenRouter key cleared");
        setSaved(true);
      } else {
        if (!apiKey.trim()) {
          toast.error("Enter a key first");
          return;
        }
        await setKeyMut.mutateAsync(apiKey);
        toast.success("OpenRouter key saved");
        setSaved(true);
      }
      setApiKey("");
      setClearKey(false);
    } catch (e) {
      toast.error("Save failed: " + errMsg(e));
    }
  };

  return (
    <section className="space-y-2">
      <div className="flex items-center justify-between">
        <HelpTip label="Stored encrypted server-side; never displayed once saved.">
          <Label className="text-sm font-medium">OpenRouter API key</Label>
        </HelpTip>
        {hasKey ? (
          <HelpTip label="Enables the OpenRouter mode button and the model picker below.">
            <Badge className="bg-emerald-500/10 text-emerald-600 border-0">
              <KeyRound className="h-3 w-3" /> key set
            </Badge>
          </HelpTip>
        ) : (
          <HelpTip label="Required before any agent can route to an OpenRouter model.">
            <Badge className="bg-amber-500/10 text-amber-600 border-0">
              <Key className="h-3 w-3" /> not set
            </Badge>
          </HelpTip>
        )}
      </div>
      <div className="flex gap-2">
        <Input
          type="password"
          value={apiKey}
          onChange={(e) => {
            setApiKey(e.target.value);
            setSaved(false);
          }}
          placeholder={
            hasKey ? "•••••••••••• (leave blank to keep)" : "sk-or-…"
          }
          disabled={clearKey || setKeyMut.isPending}
        />
        <Button onClick={handleSave} disabled={setKeyMut.isPending}>
          {setKeyMut.isPending ? "Saving…" : "Save"}
        </Button>
        {saved && !setKeyMut.isPending && (
          <Badge className="bg-emerald-500/10 text-emerald-600 border-0 self-center">
            <Check className="h-3 w-3" /> Saved
          </Badge>
        )}
      </div>
      {hasKey ? (
        <label className="flex items-center gap-2 text-xs text-muted-foreground cursor-pointer">
          <Checkbox
            checked={clearKey}
            onCheckedChange={(checked: boolean) => {
              const next = checked === true;
              setClearKey(next);
              if (next) setApiKey("");
              setSaved(false);
            }}
          />
          Clear the stored key
        </label>
      ) : (
        <p className="text-xs text-muted-foreground">
          One key unlocks hundreds of models on OpenRouter (GLM, DeepSeek, Qwen,
          Claude, GPT and more). Stored Fernet-encrypted server-side; never
          returned by the API.
        </p>
      )}
    </section>
  );
}

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : "Unknown error";
}