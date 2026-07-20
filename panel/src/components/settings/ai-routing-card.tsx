"use client";

import { useEffect, useMemo, useState, useCallback } from "react";
import {
  useApplyMode,
  useCatalog,
  useGrokKey,
  useOllamaKey,
  useRoutingMode,
  useSetGrokKey,
  useSetOllamaKey,
  useSelfHostedModels,
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
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import {
  AlertTriangle,
  Cpu,
  Key,
  KeyRound,
  Server,
  ShieldCheck,
  Sparkles,
  Zap,
} from "lucide-react";
import { toast } from "sonner";
import { AssignmentScope, AgentRole, ModelProvider } from "@/types";
import type { SelfHostedModel } from "@/lib/api/providers";
import type { RoutingMode, SelfHostedTestResult } from "@/lib/api/providers";
import { SelfHostedSection } from "@/components/settings/self-hosted-section";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { HelpTip } from "@/components/ui/help-tip";
import { Skeleton } from "@/components/ui/skeleton";
import { useAgentDefinitions } from "@/hooks/use-agents";
import {
  getBoardAgents,
  getMainPm,
  getBackendAgents,
  getFrontendAgents,
  getUxAgents,
  getSupportAgents,
  type AgentDefinition,
} from "@/lib/agent-definitions";

// Grouped to mirror the org chart in CLAUDE.md, sourced from the live
// `/api/agents` roster (useAgentDefinitions) instead of a hand-maintained
// literal — that literal drifted (missing ux-dev-2 + all 4 PR reviewers).
// CEO is excluded by construction: none of these selectors ever match it.
const AGENT_GROUP_DEFS: {
  title: string;
  titleHint: string;
  select: (agents: AgentDefinition[] | undefined | null) => AgentDefinition[];
}[] = [
  {
    title: "Board",
    titleHint: "Product Owner, Head of Marketing, Auditor",
    select: getBoardAgents,
  },
  {
    title: "Main PM",
    titleHint: "Coordinates all three delivery cells",
    select: getMainPm,
  },
  {
    title: "Backend Cell",
    titleHint: "2 Devs, 1 QA, 1 PM, 1 Documenter, 1 PR Reviewer",
    select: getBackendAgents,
  },
  {
    title: "Frontend Cell",
    titleHint: "2 Devs, 1 QA, 1 PM, 1 Documenter, 1 PR Reviewer",
    select: getFrontendAgents,
  },
  {
    title: "UX/UI Cell",
    titleHint: "2 Devs, 1 QA, 1 PM, 1 Documenter, 1 PR Reviewer",
    select: getUxAgents,
  },
  {
    title: "Intake / Secretary / PR Review",
    titleHint:
      "On-demand CEO-facing roles (Intake, Secretary) plus the root PR reviewer — reviews root→master PRs and inbound external/fork PRs.",
    select: getSupportAgents,
  },
];

// Stable within-group ordering (PM/lead first, devs, QA, doc, reviewer last)
// so the picker doesn't churn alphabetically as the live roster loads —
// ties (e.g. dev-1/dev-2) break on slug, which already sorts correctly.
const ROLE_RANK: Partial<Record<AgentRole, number>> = {
  [AgentRole.PRODUCT_OWNER]: 0,
  [AgentRole.HEAD_MARKETING]: 1,
  [AgentRole.AUDITOR]: 2,
  [AgentRole.MAIN_PM]: 0,
  [AgentRole.CELL_PM]: 0,
  [AgentRole.DEVELOPER]: 1,
  [AgentRole.QA]: 2,
  [AgentRole.DOCUMENTER]: 3,
  [AgentRole.PR_REVIEWER]: 4,
  [AgentRole.PROMPTER]: 0,
  [AgentRole.SECRETARY]: 1,
};

function byOrgOrder(a: AgentDefinition, b: AgentDefinition): number {
  const ra = (a.role && ROLE_RANK[a.role]) ?? 99;
  const rb = (b.role && ROLE_RANK[b.role]) ?? 99;
  return ra !== rb ? ra - rb : a.id.localeCompare(b.id);
}

export function AIRoutingCard() {
  const { data: catalog = [] } = useCatalog();
  const { data: keyStatus } = useOllamaKey();
  const { data: snapshot } = useRoutingMode();
  const { data: selfHostedModels = [] } = useSelfHostedModels();
  const { data: agentDefs, isLoading: agentsLoading } = useAgentDefinitions();

  const agentGroups = useMemo(
    () =>
      AGENT_GROUP_DEFS.map((g) => ({
        title: g.title,
        titleHint: g.titleHint,
        agents: g.select(agentDefs).slice().sort(byOrgOrder),
      })).filter((g) => g.agents.length > 0),
    [agentDefs],
  );

  const setKey = useSetOllamaKey();
  const applyMode = useApplyMode();

  const hasOllamaKey = !!keyStatus?.has_key;
  const currentMode: RoutingMode = snapshot?.mode ?? "anthropic";

  // Track the latest self-hosted test result so ModeButton can gate access.
  const [selfHostedTestResult, setSelfHostedTestResult] =
    useState<SelfHostedTestResult | null>(null);
  const isSelfHostedConnected = selfHostedTestResult?.ok === true;

  const handleSelfHostedTestResult = useCallback(
    (result: SelfHostedTestResult) => {
      setSelfHostedTestResult(result);
    },
    [],
  );

  // Selected self-hosted model (used when mode === 'self_hosted').
  const [selfHostedModel, setSelfHostedModel] = useState<string>("");

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

  // --- Grok (xAI) API key ---
  const { data: grokKeyStatus } = useGrokKey();
  const setGrokKeyMut = useSetGrokKey();
  const hasGrokKey = !!grokKeyStatus?.has_key;
  const [grokKey, setGrokKey] = useState("");
  const [clearGrokKey, setClearGrokKey] = useState(false);

  const saveGrokKey = async () => {
    try {
      if (clearGrokKey) {
        await setGrokKeyMut.mutateAsync("");
        toast.success("Grok key cleared");
      } else {
        if (!grokKey.trim()) {
          toast.error("Enter a key first");
          return;
        }
        await setGrokKeyMut.mutateAsync(grokKey);
        toast.success("Grok key saved");
      }
      setGrokKey("");
      setClearGrokKey(false);
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
    (c: { provider_type: ModelProvider }) =>
      c.provider_type === ModelProvider.OLLAMA_CLOUD,
  );
  const catalogGrokOnly = catalog.filter(
    (c: { provider_type: ModelProvider }) =>
      c.provider_type === ModelProvider.GROK,
  );
  const catalogAnthropicOnly = catalog.filter(
    (c: { provider_type: ModelProvider }) =>
      c.provider_type === ModelProvider.ANTHROPIC,
  );

  // --- Mode toggle handlers ---
  const flipToAnthropic = async () => {
    if (
      !confirm(
        "Switch every agent to Anthropic? Per-agent pins are kept; role/global assignments are replaced.",
      )
    )
      return;
    try {
      await applyMode.mutateAsync({ mode: "anthropic" });
      toast.success(
        "Role/global routing now on Anthropic — per-agent pins kept",
      );
    } catch (e) {
      toast.error("Switch failed: " + errMsg(e));
    }
  };

  const flipToGrok = async () => {
    if (!hasGrokKey) {
      toast.error("Save the Grok (xAI) API key first");
      return;
    }
    if (
      !confirm(
        "Switch every agent to Grok? Per-agent pins are kept; role/global assignments are replaced.",
      )
    )
      return;
    try {
      await applyMode.mutateAsync({ mode: "grok" });
      toast.success("Role/global routing now on Grok — per-agent pins kept");
    } catch (e) {
      toast.error("Switch failed: " + errMsg(e));
    }
  };

  const flipToOllama = async () => {
    if (!hasOllamaKey) {
      toast.error("Save an Ollama API key first");
      return;
    }
    if (
      !confirm(
        "Switch every agent to Ollama? Per-agent pins are kept; role/global assignments are replaced.",
      )
    )
      return;
    try {
      await applyMode.mutateAsync({ mode: "ollama" });
      toast.success("Role/global routing now on Ollama — per-agent pins kept");
    } catch (e) {
      toast.error("Switch failed: " + errMsg(e));
    }
  };

  const flipToSelfHosted = async () => {
    if (!isSelfHostedConnected) {
      toast.error("Test the self-hosted connection first");
      return;
    }
    if (
      !confirm(
        "Switch every agent to the self-hosted LLM? Per-agent pins are kept; role/global assignments are replaced.",
      )
    )
      return;
    try {
      await applyMode.mutateAsync({
        mode: "self_hosted",
        ...(selfHostedModel ? { default_model: selfHostedModel } : {}),
      });
      toast.success(
        "Role/global routing now on Self-Hosted LLM — per-agent pins kept",
      );
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
    const needsGrok = Object.values(per_agent).some((m) =>
      catalog.find(
        (c: { model_name: string; provider_type: ModelProvider }) =>
          c.model_name === m && c.provider_type === ModelProvider.GROK,
      ),
    );
    if (needsGrok && !hasGrokKey) {
      toast.error(
        "At least one agent is routed to a Grok model but no key is saved",
      );
      return;
    }
    const needsKey = Object.values(per_agent).some((m) =>
      catalog.find(
        (c: { model_name: string; provider_type: ModelProvider }) =>
          c.model_name === m && c.provider_type === ModelProvider.OLLAMA_CLOUD,
      ),
    );
    if (needsKey && !hasOllamaKey) {
      toast.error(
        "At least one agent is routed to an Ollama model but no key is saved",
      );
      return;
    }
    const needsSelfHosted = Object.values(per_agent).some((m) =>
      selfHostedModels.find((sh: SelfHostedModel) => sh.model_name === m),
    );
    if (needsSelfHosted && !isSelfHostedConnected) {
      toast.error(
        "At least one agent is routed to a self-hosted model but the connection has not been tested",
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
          <code className="px-1"> ~/.claude </code> auth; Grok (xAI) and Ollama
          Cloud use the API keys you save below; Self-Hosted connects to any
          OpenAI-compatible endpoint you run locally.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* -------- Key cards band: Grok+Ollama (left) / Self-Hosted (right) -------- */}
        <div className="grid grid-cols-1 gap-10 lg:grid-cols-2 lg:gap-14">
          <div className="space-y-8">
            {/* -------- Grok (xAI) key -------- */}
            <section className="space-y-2">
              <div className="flex items-center justify-between">
                <HelpTip label="Stored encrypted server-side; never displayed once saved.">
                  <Label className="text-sm font-medium">
                    Grok (xAI) API key
                  </Label>
                </HelpTip>
                {hasGrokKey ? (
                  <HelpTip label="Enables the Grok mode button and any Grok row in Mix mode below.">
                    <Badge className="bg-emerald-500/10 text-emerald-600 border-0">
                      <KeyRound className="h-3 w-3" /> key set
                    </Badge>
                  </HelpTip>
                ) : (
                  <HelpTip label="Required before any agent can route to a Grok model.">
                    <Badge className="bg-amber-500/10 text-amber-600 border-0">
                      <Key className="h-3 w-3" /> not set
                    </Badge>
                  </HelpTip>
                )}
              </div>
              <div className="flex gap-2">
                <Input
                  type="password"
                  value={grokKey}
                  onChange={(e) => setGrokKey(e.target.value)}
                  placeholder={
                    hasGrokKey ? "•••••••••••• (leave blank to keep)" : "xai-…"
                  }
                  disabled={clearGrokKey}
                />
                <Button
                  onClick={saveGrokKey}
                  disabled={setGrokKeyMut.isPending}
                >
                  {setGrokKeyMut.isPending ? "Saving…" : "Save"}
                </Button>
              </div>
              {hasGrokKey ? (
                <label className="flex items-center gap-2 text-xs text-muted-foreground cursor-pointer">
                  <Checkbox
                    checked={clearGrokKey}
                    onCheckedChange={(checked: boolean) => {
                      const next = checked === true;
                      setClearGrokKey(next);
                      if (next) setGrokKey("");
                    }}
                  />
                  Clear the stored key
                </label>
              ) : (
                <p className="text-xs text-muted-foreground">
                  Used for grok-build-0.1 at api.x.ai/v1. Stored
                  Fernet-encrypted server-side; never returned by the API.
                </p>
              )}
            </section>

            <Separator />

            {/* -------- Ollama key -------- */}
            <section className="space-y-2">
              <div className="flex items-center justify-between">
                <HelpTip label="Stored encrypted server-side; never displayed once saved.">
                  <Label className="text-sm font-medium">
                    Ollama Cloud API key
                  </Label>
                </HelpTip>
                {hasOllamaKey ? (
                  <HelpTip label="Enables the Ollama mode button and any Ollama row in Mix mode below.">
                    <Badge className="bg-emerald-500/10 text-emerald-600 border-0">
                      <KeyRound className="h-3 w-3" /> key set
                    </Badge>
                  </HelpTip>
                ) : (
                  <HelpTip label="Required before any agent can route to an Ollama Cloud model.">
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
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder={
                    hasOllamaKey
                      ? "•••••••••••• (leave blank to keep)"
                      : "ollama_xxx…"
                  }
                  disabled={clearKey}
                />
                <Button onClick={saveKey} disabled={setKey.isPending}>
                  {setKey.isPending ? "Saving…" : "Save"}
                </Button>
              </div>
              {hasOllamaKey ? (
                <label className="flex items-center gap-2 text-xs text-muted-foreground cursor-pointer">
                  <Checkbox
                    checked={clearKey}
                    onCheckedChange={(checked: boolean) => {
                      const next = checked === true;
                      setClearKey(next);
                      if (next) setApiKey("");
                    }}
                  />
                  Clear the stored key
                </label>
              ) : (
                <p className="text-xs text-muted-foreground">
                  Stored Fernet-encrypted server-side; never returned by the
                  API.
                </p>
              )}
            </section>
          </div>

          {/* -------- Self-Hosted LLM -------- */}
          <SelfHostedSection
            testResult={selfHostedTestResult}
            onTestResult={handleSelfHostedTestResult}
            onTestSuccess={() => undefined}
          />
        </div>

        <Separator />

        {/* -------- Mode toggle -------- */}
        <section className="space-y-3">
          <HelpTip label="Anthropic / Grok / Ollama / Self-Hosted replace role/global routing with that provider; per-agent pins in the table below survive the switch. Mix keeps whatever's picked in the table.">
            <Label className="text-sm font-medium">Routing mode</Label>
          </HelpTip>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-2">
            <ModeButton
              icon={<ShieldCheck className="h-4 w-4" />}
              label="Anthropic"
              description="Every agent uses Anthropic (via mounted ~/.claude)."
              active={currentMode === "anthropic"}
              onClick={flipToAnthropic}
              disabled={applyMode.isPending}
            />
            <ModeButton
              icon={<Zap className="h-4 w-4" />}
              label="Grok"
              description={
                hasGrokKey
                  ? "Every agent uses Grok (grok-build-0.1)."
                  : "Save the Grok (xAI) key first."
              }
              active={currentMode === "grok"}
              onClick={flipToGrok}
              disabled={applyMode.isPending || !hasGrokKey}
            />
            <ModeButton
              icon={<Sparkles className="h-4 w-4" />}
              label="Ollama"
              description={
                hasOllamaKey
                  ? "Every agent uses Ollama Cloud." // TODO: Add dynamic default model name based on llm_catalog.py
                  : "Save the Ollama key first."
              }
              active={currentMode === "ollama"}
              onClick={flipToOllama}
              disabled={applyMode.isPending || !hasOllamaKey}
            />
            <ModeButton
              icon={<Server className="h-4 w-4" />}
              label="Self-Hosted"
              description={
                isSelfHostedConnected
                  ? "Every agent uses your self-hosted LLM endpoint."
                  : "Test the connection above first."
              }
              active={currentMode === "self_hosted"}
              onClick={flipToSelfHosted}
              disabled={applyMode.isPending || !isSelfHostedConnected}
              labelHint="Unlike Ollama/Grok, self-hosted has no built-in fallback model — pick one in the picker below or the switch fails server-side."
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
              Some agents may already be routed to Ollama but no key is saved —
              those agents will fall back to Anthropic at spawn.
            </p>
          ) : null}
          {currentMode === "grok" || currentMode === "mix" ? (
            <p className="text-xs text-muted-foreground">
              Grok agents run on xAI&apos;s official grok CLI; the command /
              secret-exfiltration guard, the prompt-injection guard, and the
              per-agent cost cap all apply.
            </p>
          ) : null}
        </section>

        {/* -------- Self-Hosted model picker (when self_hosted mode active) -------- */}
        {currentMode === "self_hosted" && (
          <>
            <Separator />
            <section className="space-y-2">
              <HelpTip label="Self-hosted has no automatic fallback — clearing this back to '(use server default)' will fail the next time Self-Hosted mode is applied.">
                <Label className="text-sm font-medium">
                  Self-Hosted default model
                </Label>
              </HelpTip>
              <p className="text-xs text-muted-foreground">
                Choose which discovered model all agents should use in
                self-hosted mode.
              </p>
              <Select
                value={selfHostedModel || "__clear__"}
                onValueChange={(v: string) =>
                  setSelfHostedModel(v === "__clear__" ? "" : v)
                }
              >
                <SelectTrigger className="w-full max-w-sm">
                  <SelectValue placeholder="(use server default)" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__clear__">
                    (use server default)
                  </SelectItem>
                  {selfHostedModels.map((m: SelfHostedModel) => (
                    <SelectItem key={m.model_name} value={m.model_name}>
                      {m.display_name}
                      {m.display_name !== m.model_name
                        ? ` — ${m.model_name}`
                        : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </section>
          </>
        )}

        {/* -------- Mix-mode per-agent picker -------- */}
        <Separator />
        <section className="space-y-3">
          <div className="flex items-center justify-between">
            <HelpTip label="A blank row falls back to that agent's role default, then the last global mode's model — not a separate 'mix default'.">
              <Label className="text-sm font-medium">
                Per-agent override (mix mode)
              </Label>
            </HelpTip>
            <Button size="sm" onClick={saveMix} disabled={applyMode.isPending}>
              {applyMode.isPending ? "Saving…" : "Save mix"}
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            Leave a row blank to inherit from the global mode. Saving overwrites
            all per-agent overrides with what&apos;s picked here.
          </p>
          {agentsLoading ? (
            <div className="divide-y rounded-md border">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="p-4">
                  <Skeleton className="mb-2 h-3 w-24" />
                  <div className="grid grid-cols-1 gap-x-8 gap-y-3 sm:grid-cols-2">
                    <Skeleton className="h-14 w-full rounded-md" />
                    <Skeleton className="h-14 w-full rounded-md" />
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="divide-y rounded-md border">
              {agentGroups.map((group) => (
                <div key={group.title} className="p-4">
                  <HelpTip label={group.titleHint}>
                    <h4 className="mb-2 w-fit text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                      {group.title}
                    </h4>
                  </HelpTip>
                  <div className="grid grid-cols-1 gap-x-8 gap-y-3 sm:grid-cols-2">
                    {group.agents.map((a) => (
                      <div
                        key={a.id}
                        className="grid grid-cols-[1fr_170px] items-center gap-4 rounded-md border px-3 py-2.5"
                      >
                        <div className="min-w-0">
                          <div className="truncate font-mono text-xs">
                            {a.id}
                          </div>
                          <div className="truncate text-[11px] text-muted-foreground">
                            {a.name}
                          </div>
                        </div>
                        <Select
                          value={mixMap[a.id] ?? ""}
                          onValueChange={(v: string) =>
                            setMixMap((prev) => ({
                              ...prev,
                              [a.id]: v === "__clear__" ? "" : v,
                            }))
                          }
                        >
                          <SelectTrigger size="sm" className="w-full text-xs">
                            <SelectValue placeholder="(inherit)" />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="__clear__">
                              (inherit global)
                            </SelectItem>

                            {/* Anthropic models */}
                            {catalogAnthropicOnly.length > 0 && (
                              <SelectGroup>
                                <SelectLabel>
                                  <ProviderBadge variant="anthropic" />
                                  Anthropic
                                </SelectLabel>
                                {catalogAnthropicOnly.map(
                                  (c: {
                                    model_name: string;
                                    display_name: string;
                                  }) => (
                                    <SelectItem
                                      key={c.model_name}
                                      value={c.model_name}
                                    >
                                      {c.display_name}
                                    </SelectItem>
                                  ),
                                )}
                              </SelectGroup>
                            )}

                            {/* Grok (xAI) models */}
                            {catalogGrokOnly.length > 0 && (
                              <SelectGroup>
                                <SelectLabel>
                                  <ProviderBadge variant="grok" />
                                  Grok (xAI)
                                </SelectLabel>
                                {catalogGrokOnly.map(
                                  (c: {
                                    model_name: string;
                                    display_name: string;
                                  }) => (
                                    <SelectItem
                                      key={c.model_name}
                                      value={c.model_name}
                                    >
                                      {c.display_name}
                                    </SelectItem>
                                  ),
                                )}
                              </SelectGroup>
                            )}

                            {/* Ollama Cloud models */}
                            {catalogOllamaOnly.length > 0 && (
                              <SelectGroup>
                                <SelectLabel>
                                  <ProviderBadge variant="ollama" />
                                  Ollama Cloud
                                </SelectLabel>
                                {catalogOllamaOnly.map(
                                  (c: {
                                    model_name: string;
                                    display_name: string;
                                  }) => (
                                    <SelectItem
                                      key={c.model_name}
                                      value={c.model_name}
                                    >
                                      {c.display_name}
                                    </SelectItem>
                                  ),
                                )}
                              </SelectGroup>
                            )}

                            {/* Self-Hosted models */}
                            {selfHostedModels.length > 0 && (
                              <SelectGroup>
                                <SelectLabel>
                                  <ProviderBadge variant="self-hosted" />
                                  Self-Hosted
                                </SelectLabel>
                                {selfHostedModels.map((m: SelfHostedModel) => (
                                  <SelectItem
                                    key={m.model_name}
                                    value={m.model_name}
                                  >
                                    {m.display_name}
                                  </SelectItem>
                                ))}
                              </SelectGroup>
                            )}

                            {/* Fallback: un-grouped catalog when no grouping is possible */}
                            {catalogAnthropicOnly.length === 0 &&
                              catalogOllamaOnly.length === 0 &&
                              selfHostedModels.length === 0 &&
                              catalogForMix.map(
                                (c: {
                                  model_name: string;
                                  display_name: string;
                                }) => (
                                  <SelectItem
                                    key={c.model_name}
                                    value={c.model_name}
                                  >
                                    {c.display_name} — {c.model_name}
                                  </SelectItem>
                                ),
                              )}
                          </SelectContent>
                        </Select>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
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
  labelHint,
}: {
  icon: React.ReactNode;
  label: string;
  description: string;
  active: boolean;
  disabled: boolean;
  onClick: () => void | Promise<void>;
  highlight?: boolean;
  /** Extra hover context on the label, e.g. a gotcha the always-visible description doesn't cover. */
  labelHint?: string;
}) {
  return (
    <Button
      type="button"
      variant="outline"
      onClick={onClick}
      disabled={disabled}
      className={
        "h-auto flex-col items-start justify-start p-3 whitespace-normal " +
        (active || highlight ? "border-primary bg-primary/5" : "")
      }
    >
      <div className="flex w-full items-center gap-2 text-sm font-medium">
        {icon}
        <HelpTip label={labelHint}>
          <span>{label}</span>
        </HelpTip>
        {active ? (
          <HelpTip label="Currently applied fleet-wide — every agent spawns on this provider until the mode changes again.">
            <Badge className="ml-auto bg-primary/15 text-primary border-0 text-xs">
              active
            </Badge>
          </HelpTip>
        ) : null}
      </div>
      <p className="mt-1 text-xs text-muted-foreground font-normal">
        {description}
      </p>
    </Button>
  );
}

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : "Unknown error";
}

// ---------------------------------------------------------------------------
// Small colored pill to distinguish providers in the Mix mode dropdown.
// ---------------------------------------------------------------------------

function ProviderBadge({
  variant,
}: {
  variant: "anthropic" | "grok" | "ollama" | "self-hosted";
}) {
  const styles: Record<string, string> = {
    anthropic: "bg-blue-500/20 text-blue-700 dark:text-blue-400",
    ollama: "bg-violet-500/20 text-violet-700 dark:text-violet-400",
    "self-hosted": "bg-purple-500/20 text-purple-700 dark:text-purple-400",
    grok: "bg-teal-500/20 text-teal-700 dark:text-teal-400",
  };
  const labels: Record<string, string> = {
    anthropic: "A",
    ollama: "O",
    "self-hosted": "S",
    grok: "G",
  };
  return (
    <span
      className={
        "mr-1 inline-flex h-4 w-4 items-center justify-center rounded-full text-[10px] font-bold " +
        (styles[variant] ?? "")
      }
    >
      {labels[variant]}
    </span>
  );
}
