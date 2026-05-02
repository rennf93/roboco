"use client";

import { useEffect, useMemo, useState } from "react";
import {
  useApplyMode,
  useCatalog,
  useOllamaKey,
  useRoutingMode,
  useSetOllamaKey,
} from "@/hooks/use-providers";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import {
  AlertTriangle,
  Cpu,
  Key,
  KeyRound,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { toast } from "sonner";
import { AssignmentScope, ModelProvider } from "@/types";
import type { RoutingMode } from "@/lib/api/providers";

// Matches the roboco agents_config AGENT_ROLE_MAP / AGENT_TEAM_MAP.
// Hard-coded so Mix mode shows a stable 18-row picker without an extra
// server round-trip. Order mirrors the org chart in CLAUDE.md.
//
// NOTE: CEO is explicitly excluded — it's the human-in-the-loop seat
// (Renzo), not an LLM-backed agent. Routing it anywhere would be a
// no-op in spawn_agent but confusing in the UI.
const AGENTS: { slug: string; label: string }[] = [
  { slug: "product-owner", label: "Product Owner" },
  { slug: "head-marketing", label: "Head of Marketing" },
  { slug: "auditor", label: "Auditor" },
  { slug: "main-pm", label: "Main PM" },
  { slug: "be-pm", label: "Backend PM" },
  { slug: "be-dev-1", label: "Backend Dev 1" },
  { slug: "be-dev-2", label: "Backend Dev 2" },
  { slug: "be-qa", label: "Backend QA" },
  { slug: "be-doc", label: "Backend Documenter" },
  { slug: "fe-pm", label: "Frontend PM" },
  { slug: "fe-dev-1", label: "Frontend Dev 1" },
  { slug: "fe-dev-2", label: "Frontend Dev 2" },
  { slug: "fe-qa", label: "Frontend QA" },
  { slug: "fe-doc", label: "Frontend Documenter" },
  { slug: "ux-pm", label: "UX/UI PM" },
  { slug: "ux-dev-1", label: "UX/UI Dev" },
  { slug: "ux-qa", label: "UX/UI QA" },
  { slug: "ux-doc", label: "UX/UI Documenter" },
];

export function AIRoutingCard() {
  const { data: catalog = [] } = useCatalog();
  const { data: keyStatus } = useOllamaKey();
  const { data: snapshot } = useRoutingMode();

  const setKey = useSetOllamaKey();
  const applyMode = useApplyMode();

  const hasOllamaKey = !!keyStatus?.has_key;
  const currentMode: RoutingMode = snapshot?.mode ?? "anthropic";

  // --- API key input ---
  const [apiKey, setApiKey] = useState("");
  const [clearKey, setClearKey] = useState(false);

  const saveKey = async () => {
    try {
      if (clearKey) {
        await setKey.mutateAsync("");
        toast.success("Ollama key cleared");
      } else {
        if (!apiKey.trim()) {
          toast.error("Enter a key first");
          return;
        }
        await setKey.mutateAsync(apiKey);
        toast.success("Ollama key saved");
      }
      setApiKey("");
      setClearKey(false);
    } catch (e) {
      toast.error("Save failed: " + errMsg(e));
    }
  };

  // --- Mix mode state: agent_slug → model_name ---
  const initialMix = useMemo(() => {
    const map: Record<string, string> = {};
    for (const a of snapshot?.assignments ?? []) {
      if (a.scope === AssignmentScope.AGENT_SLUG && a.scope_value) {
        map[a.scope_value] = a.model_name;
      }
    }
    return map;
  }, [snapshot]);

  const [mixMap, setMixMap] = useState<Record<string, string>>(initialMix);
  useEffect(() => {
    // Reset local state when server returns a fresh snapshot (after save,
    // mode switch, or initial load).
    setMixMap(initialMix);
  }, [initialMix]);

  const catalogForMix = catalog;
  const catalogOllamaOnly = catalog.filter(
    (c: { provider_type: ModelProvider }) => c.provider_type === ModelProvider.OLLAMA_CLOUD,
  );

  // --- Mode toggle handlers ---
  const flipToAnthropic = async () => {
    if (!confirm("Switch every agent to Anthropic? Clears any overrides.")) return;
    try {
      await applyMode.mutateAsync({ mode: "anthropic" });
      toast.success("All agents now on Anthropic");
    } catch (e) {
      toast.error("Switch failed: " + errMsg(e));
    }
  };

  const flipToOllama = async () => {
    if (!hasOllamaKey) {
      toast.error("Save an Ollama API key first");
      return;
    }
    if (!confirm("Switch every agent to Ollama? Clears any overrides.")) return;
    try {
      await applyMode.mutateAsync({ mode: "ollama" });
      toast.success("All agents now on Ollama");
    } catch (e) {
      toast.error("Switch failed: " + errMsg(e));
    }
  };

  const saveMix = async () => {
    // Filter out empty picks (nothing selected = inherit global).
    const per_agent: Record<string, string> = {};
    for (const [slug, model] of Object.entries(mixMap)) {
      if (model) per_agent[slug] = model;
    }
    if (Object.keys(per_agent).length === 0) {
      toast.error("Pick a model for at least one agent");
      return;
    }
    const needsKey = Object.values(per_agent).some((m) =>
      catalog.find(
        (c: { model_name: string; provider_type: ModelProvider }) =>
          c.model_name === m &&
          c.provider_type === ModelProvider.OLLAMA_CLOUD,
      ),
    );
    if (needsKey && !hasOllamaKey) {
      toast.error(
        "At least one agent is routed to an Ollama model but no key is saved",
      );
      return;
    }
    try {
      await applyMode.mutateAsync({ mode: "mix", per_agent });
      toast.success("Per-agent routing saved");
    } catch (e) {
      toast.error("Save failed: " + errMsg(e));
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Cpu className="h-5 w-5" /> AI Routing
        </CardTitle>
        <CardDescription>
          Decide which model backs each agent. Anthropic uses the mounted
          <code className="px-1"> ~/.claude </code> auth; Ollama Cloud uses
          the API key you save below.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* -------- Ollama key -------- */}
        <section className="space-y-2">
          <div className="flex items-center justify-between">
            <Label className="text-sm font-medium">Ollama Cloud API key</Label>
            {hasOllamaKey ? (
              <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-xs font-medium text-emerald-600">
                <KeyRound className="h-3 w-3" /> key set
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-2 py-0.5 text-xs font-medium text-amber-600">
                <Key className="h-3 w-3" /> not set
              </span>
            )}
          </div>
          <div className="flex gap-2">
            <Input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={
                hasOllamaKey ? "•••••••••••• (leave blank to keep)" : "ollama_xxx…"
              }
              disabled={clearKey}
            />
            <Button onClick={saveKey} disabled={setKey.isPending}>
              {setKey.isPending ? "Saving…" : "Save"}
            </Button>
          </div>
          {hasOllamaKey ? (
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={clearKey}
                onChange={(e) => {
                  setClearKey(e.target.checked);
                  if (e.target.checked) setApiKey("");
                }}
              />
              Clear the stored key
            </label>
          ) : (
            <p className="text-xs text-muted-foreground">
              Stored Fernet-encrypted server-side; never returned by the API.
            </p>
          )}
        </section>

        <Separator />

        {/* -------- Mode toggle -------- */}
        <section className="space-y-3">
          <Label className="text-sm font-medium">Routing mode</Label>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
            <ModeButton
              icon={<ShieldCheck className="h-4 w-4" />}
              label="Anthropic"
              description="Every agent uses Anthropic (via mounted ~/.claude)."
              active={currentMode === "anthropic"}
              onClick={flipToAnthropic}
              disabled={applyMode.isPending}
            />
            <ModeButton
              icon={<Sparkles className="h-4 w-4" />}
              label="Ollama"
              description={
                hasOllamaKey
                  ? "Every agent uses Ollama Cloud (Minimax M2.7 default)."
                  : "Save the Ollama key first."
              }
              active={currentMode === "ollama"}
              onClick={flipToOllama}
              disabled={applyMode.isPending || !hasOllamaKey}
            />
            <ModeButton
              icon={<Cpu className="h-4 w-4" />}
              label="Mix"
              description="Pick a model per agent (table appears below)."
              active={currentMode === "mix"}
              // "Mix" is engaged by picking models + Save — not a direct
              // toggle. Clicking just scrolls awareness.
              onClick={() => undefined}
              disabled={false}
              highlight={currentMode === "mix"}
            />
          </div>
          {currentMode === "mix" && !hasOllamaKey ? (
            <p className="text-xs text-amber-600 flex items-center gap-1">
              <AlertTriangle className="h-3 w-3" />
              Some agents may already be routed to Ollama but no key is
              saved — those agents will fall back to Anthropic at spawn.
            </p>
          ) : null}
        </section>

        {/* -------- Mix-mode per-agent picker -------- */}
        <Separator />
        <section className="space-y-3">
          <div className="flex items-center justify-between">
            <Label className="text-sm font-medium">
              Per-agent override (mix mode)
            </Label>
            <Button
              size="sm"
              onClick={saveMix}
              disabled={applyMode.isPending}
            >
              {applyMode.isPending ? "Saving…" : "Save mix"}
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            Leave a row blank to inherit from the global mode. Saving
            overwrites all per-agent overrides with what&apos;s picked here.
          </p>
          <div className="divide-y rounded-md border">
            {AGENTS.map((a) => (
              <div
                key={a.slug}
                className="grid grid-cols-[1fr_280px] items-center gap-2 px-3 py-2"
              >
                <div>
                  <div className="font-mono text-sm">{a.slug}</div>
                  <div className="text-xs text-muted-foreground">
                    {a.label}
                  </div>
                </div>
                <Select
                  value={mixMap[a.slug] ?? ""}
                  onValueChange={(v: string) =>
                    setMixMap((prev) => ({
                      ...prev,
                      [a.slug]: v === "__clear__" ? "" : v,
                    }))
                  }
                >
                  <SelectTrigger>
                    <SelectValue placeholder="(inherit)" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__clear__">(inherit global)</SelectItem>
                    {catalogForMix.map((c: { model_name: string; display_name: string }) => (
                      <SelectItem key={c.model_name} value={c.model_name}>
                        {c.display_name} — {c.model_name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            ))}
          </div>
          {catalogOllamaOnly.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              Ollama catalog empty — check /api/providers/catalog.
            </p>
          ) : null}
        </section>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------

function ModeButton({
  icon,
  label,
  description,
  active,
  disabled,
  onClick,
  highlight,
}: {
  icon: React.ReactNode;
  label: string;
  description: string;
  active: boolean;
  disabled: boolean;
  onClick: () => void | Promise<void>;
  highlight?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={
        "rounded-md border p-3 text-left transition-colors " +
        (active || highlight
          ? "border-primary bg-primary/5"
          : "hover:bg-muted") +
        (disabled ? " cursor-not-allowed opacity-50" : "")
      }
    >
      <div className="flex items-center gap-2 text-sm font-medium">
        {icon}
        <span>{label}</span>
        {active ? (
          <span className="ml-auto rounded-full bg-primary/15 px-2 py-0.5 text-xs text-primary">
            active
          </span>
        ) : null}
      </div>
      <p className="mt-1 text-xs text-muted-foreground">{description}</p>
    </button>
  );
}

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : "Unknown error";
}
