"use client";

import { useEffect, useMemo, useState, useCallback } from "react";
import {
  useApplyMode,
  useApplyPreset,
  useCatalog,
  useComplexityOverrides,
  useDeleteComplexityOverride,
  useDeletePreset,
  useGrokKey,
  useOllamaKey,
  useRoutingMode,
  useRoutingPresets,
  useSavePreset,
  useSetComplexityOverride,
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
  Bot,
  Cpu,
  Gauge,
  Gem,
  Key,
  KeyRound,
  Server,
  ShieldCheck,
  Sparkles,
  Zap,
} from "lucide-react";
import { toast } from "sonner";
import { AssignmentScope, AgentRole, ModelProvider } from "@/types";
import {
  COMPLEXITY_OVERRIDE_ROLES,
  type ComplexityLevel,
  type SelfHostedModel,
} from "@/lib/api/providers";
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

// Codex/Gemini are V1 delivery-roles-only — no interactive Intake/Secretary
// support (see roboco.llm.providers.codex / .gemini). This group's per-agent
// picker excludes both providers below instead of offering a route that
// would silently misroute the persistent Intake/Secretary session at spawn.
const INTERACTIVE_ONLY_GROUP_TITLE = "Intake / Secretary / PR Review";

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

// Display labels for the complexity-override allowlist — mirrors the
// backend's fixed `_COMPLEXITY_OVERRIDE_ROLES` (developer/qa/documenter).
// cell_pm is deliberately excluded (a coordinator role — see
// COMPLEXITY_OVERRIDE_ROLES), along with every other coordinator/board/
// CEO-facing role.
const COMPLEXITY_ROLE_LABELS: Record<string, string> = {
  developer: "Developer",
  qa: "QA",
  documenter: "Documenter",
};

export function AIRoutingCard() {
  const { data: catalog = [] } = useCatalog();
  const { data: keyStatus } = useOllamaKey();
  const { data: snapshot } = useRoutingMode();
  const { data: selfHostedModels = [] } = useSelfHostedModels();
  const { data: complexityOverrides = [] } = useComplexityOverrides();
  const setComplexityOverride = useSetComplexityOverride();
  const deleteComplexityOverride = useDeleteComplexityOverride();
  const { data: presets = [] } = useRoutingPresets();
  const savePreset = useSavePreset();
  const applyPreset = useApplyPreset();
  const deletePreset = useDeletePreset();
  const {
    data: agentDefs,
    isLoading: agentsLoading,
    isError: agentsError,
  } = useAgentDefinitions();

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
  const catalogOpenaiOnly = catalog.filter(
    (c: { provider_type: ModelProvider }) =>
      c.provider_type === ModelProvider.OPENAI,
  );
  const catalogGeminiOnly = catalog.filter(
    (c: { provider_type: ModelProvider }) =>
      c.provider_type === ModelProvider.GEMINI,
  );
  const catalogAnthropicOnly = catalog.filter(
    (c: { provider_type: ModelProvider }) =>
      c.provider_type === ModelProvider.ANTHROPIC,
  );

  // --- Mode toggle handlers ---
  const flipToAnthropic = async () => {
    if (
      !confirm(
        "Switch every agent to Anthropic? Per-agent pins and complexity " +
          "overrides are kept; other role/global assignments are replaced.",
      )
    )
      return;
    try {
      await applyMode.mutateAsync({ mode: "anthropic" });
      toast.success(
        "Role/global routing now on Anthropic — per-agent pins and complexity overrides kept",
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
        "Switch every agent to Grok? Per-agent pins and complexity " +
          "overrides are kept; other role/global assignments are replaced.",
      )
    )
      return;
    try {
      await applyMode.mutateAsync({ mode: "grok" });
      toast.success(
        "Role/global routing now on Grok — per-agent pins and complexity overrides kept",
      );
    } catch (e) {
      toast.error("Switch failed: " + errMsg(e));
    }
  };

  const flipToCodex = async () => {
    if (
      !confirm(
        "Switch every agent to Codex? Per-agent pins and complexity " +
          "overrides are kept; other role/global assignments are replaced.",
      )
    )
      return;
    try {
      await applyMode.mutateAsync({ mode: "codex" });
      toast.success(
        "Role/global routing now on Codex — per-agent pins and complexity overrides kept",
      );
    } catch (e) {
      toast.error("Switch failed: " + errMsg(e));
    }
  };

  const flipToGemini = async () => {
    if (
      !confirm(
        "Switch every agent to Gemini? Per-agent pins and complexity " +
          "overrides are kept; other role/global assignments are replaced.",
      )
    )
      return;
    try {
      await applyMode.mutateAsync({ mode: "gemini" });
      toast.success(
        "Role/global routing now on Gemini — per-agent pins and complexity overrides kept",
      );
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
        "Switch every agent to Ollama? Per-agent pins and complexity " +
          "overrides are kept; other role/global assignments are replaced.",
      )
    )
      return;
    try {
      await applyMode.mutateAsync({ mode: "ollama" });
      toast.success(
        "Role/global routing now on Ollama — per-agent pins and complexity overrides kept",
      );
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
        "Switch every agent to the self-hosted LLM? Per-agent pins and " +
          "complexity overrides are kept; other role/global assignments " +
          "are replaced.",
      )
    )
      return;
    try {
      await applyMode.mutateAsync({
        mode: "self_hosted",
        ...(selfHostedModel ? { default_model: selfHostedModel } : {}),
      });
      toast.success(
        "Role/global routing now on Self-Hosted LLM — per-agent pins and complexity overrides kept",
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

  // --- Cost-tiered defaults (additive seed — never wipes existing routing) ---
  const flipToCostTiered = async () => {
    if (
      !confirm(
        "Seed the day-1 cost-tiered default (developer:low → Haiku)? " +
          "Unlike the other buttons this is additive — it does not clear " +
          "any existing routing.",
      )
    )
      return;
    try {
      await applyMode.mutateAsync({ mode: "cost_tiered" });
      toast.success(
        "Cost-tiered default seeded (developer:low → Haiku)",
      );
    } catch (e) {
      toast.error("Apply failed: " + errMsg(e));
    }
  };

  // --- Complexity overrides (compound ROLE(":"complexity) rows) ---
  const complexityMap = useMemo(() => {
    const map: Record<string, Partial<Record<ComplexityLevel, string>>> = {};
    for (const o of complexityOverrides) {
      map[o.role] = { ...map[o.role], [o.complexity]: o.model_name };
    }
    return map;
  }, [complexityOverrides]);

  const handleComplexityChange = async (
    role: string,
    complexity: ComplexityLevel,
    modelName: string,
  ) => {
    try {
      if (!modelName) {
        if (complexityMap[role]?.[complexity]) {
          await deleteComplexityOverride.mutateAsync({ role, complexity });
          toast.success(`Cleared ${role}:${complexity} override`);
        }
        return;
      }
      const result = await setComplexityOverride.mutateAsync({
        role,
        complexity,
        model_name: modelName,
      });
      toast.success(`${role}:${complexity} → ${modelName}`);
      // Allowed but never silent: a cross-provider-family override (e.g. an
      // Anthropic role pinned to a Grok/Ollama/self-hosted model) also gets
      // its own warning toast.
      if (result.warning) {
        toast.warning(result.warning);
      }
    } catch (e) {
      toast.error("Save failed: " + errMsg(e));
    }
  };

  // --- Routing presets (named, full snapshots) ---
  const [selectedPresetId, setSelectedPresetId] = useState("");
  const [showPresetNameInput, setShowPresetNameInput] = useState(false);
  const [presetNameDraft, setPresetNameDraft] = useState("");
  const selectedPreset = presets.find((p) => p.id === selectedPresetId);

  const handleSavePreset = async () => {
    const name = presetNameDraft.trim();
    if (!name) {
      toast.error("Enter a preset name first");
      return;
    }
    try {
      const saved = await savePreset.mutateAsync(name);
      toast.success(`Saved preset "${saved.name}"`);
      setPresetNameDraft("");
      setShowPresetNameInput(false);
      setSelectedPresetId(saved.id);
    } catch (e) {
      toast.error("Save failed: " + errMsg(e));
    }
  };

  const handleApplyPreset = async () => {
    if (!selectedPresetId) {
      toast.error("Pick a preset first");
      return;
    }
    if (
      !confirm(
        `Apply preset "${selectedPreset?.name ?? selectedPresetId}"? This ` +
          "replaces the ENTIRE current routing state (every per-agent pin, " +
          "role row, and global default) with the saved snapshot.",
      )
    )
      return;
    try {
      const result = await applyPreset.mutateAsync(selectedPresetId);
      if (result.skipped.length > 0) {
        toast.error(
          `Applied with ${result.skipped.length} row(s) skipped: ` +
            result.skipped.join("; "),
        );
      } else {
        toast.success("Preset applied");
      }
    } catch (e) {
      toast.error("Apply failed: " + errMsg(e));
    }
  };

  const handleDeletePreset = async () => {
    if (!selectedPresetId) {
      toast.error("Pick a preset first");
      return;
    }
    if (!confirm(`Delete preset "${selectedPreset?.name ?? selectedPresetId}"?`))
      return;
    try {
      await deletePreset.mutateAsync(selectedPresetId);
      toast.success("Preset deleted");
      setSelectedPresetId("");
    } catch (e) {
      toast.error("Delete failed: " + errMsg(e));
    }
  };

  // The full per-agent model-picker option list, shared by every group's
  // Select — factored out so the Codex/Gemini exclusion for the interactive
  // group (`restrictInteractiveOnly`) doesn't require duplicating the whole
  // catalog-grouped SelectContent tree.
  const renderMixSelectOptions = (restrictInteractiveOnly: boolean) => (
    <>
      <SelectItem value="__clear__">(inherit global)</SelectItem>

      {/* Anthropic models */}
      {catalogAnthropicOnly.length > 0 && (
        <SelectGroup>
          <SelectLabel>
            <ProviderBadge variant="anthropic" />
            Anthropic
          </SelectLabel>
          {catalogAnthropicOnly.map(
            (c: { model_name: string; display_name: string }) => (
              <SelectItem key={c.model_name} value={c.model_name}>
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
            (c: { model_name: string; display_name: string }) => (
              <SelectItem key={c.model_name} value={c.model_name}>
                {c.display_name}
              </SelectItem>
            ),
          )}
        </SelectGroup>
      )}

      {/* Codex (OpenAI) models — excluded for the interactive-only group */}
      {!restrictInteractiveOnly && catalogOpenaiOnly.length > 0 && (
        <SelectGroup>
          <SelectLabel>
            <ProviderBadge variant="openai" />
            Codex (OpenAI)
          </SelectLabel>
          {catalogOpenaiOnly.map(
            (c: { model_name: string; display_name: string }) => (
              <SelectItem key={c.model_name} value={c.model_name}>
                {c.display_name}
              </SelectItem>
            ),
          )}
        </SelectGroup>
      )}

      {/* Gemini (Google) models — excluded for the interactive-only group */}
      {!restrictInteractiveOnly && catalogGeminiOnly.length > 0 && (
        <SelectGroup>
          <SelectLabel>
            <ProviderBadge variant="gemini" />
            Gemini (Google)
          </SelectLabel>
          {catalogGeminiOnly.map(
            (c: { model_name: string; display_name: string }) => (
              <SelectItem key={c.model_name} value={c.model_name}>
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
            (c: { model_name: string; display_name: string }) => (
              <SelectItem key={c.model_name} value={c.model_name}>
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
            <SelectItem key={m.model_name} value={m.model_name}>
              {m.display_name}
            </SelectItem>
          ))}
        </SelectGroup>
      )}

      {/* Fallback: un-grouped catalog when no grouping is possible */}
      {catalogAnthropicOnly.length === 0 &&
        catalogOllamaOnly.length === 0 &&
        selfHostedModels.length === 0 &&
        catalogForMix.map((c: { model_name: string; display_name: string }) => (
          <SelectItem key={c.model_name} value={c.model_name}>
            {c.display_name} — {c.model_name}
          </SelectItem>
        ))}
    </>
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Cpu className="h-5 w-5" /> AI Routing
        </CardTitle>
        <CardDescription>
          Decide which model backs each agent. Anthropic uses the mounted
          <code className="px-1"> ~/.claude </code> auth; Grok (xAI) and Ollama
          Cloud use the API keys you save below; Codex and Gemini authenticate
          via their own mounted CLI subscriptions (no key needed) — V1:
          delivery roles only, not Intake/Secretary; Self-Hosted connects to
          any OpenAI-compatible endpoint you run locally.
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
          <HelpTip label="Anthropic / Grok / Codex / Gemini / Ollama / Self-Hosted replace role/global routing with that provider; per-agent pins in the table below survive the switch. Mix keeps whatever's picked in the table.">
            <Label className="text-sm font-medium">Routing mode</Label>
          </HelpTip>
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-2">
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
              icon={<Bot className="h-4 w-4" />}
              label="Codex"
              description="Every agent uses Codex (gpt-5.3-codex)."
              active={currentMode === "codex"}
              onClick={flipToCodex}
              disabled={applyMode.isPending}
              labelHint="Codex authenticates via a mounted ~/.codex subscription (ChatGPT, no API key) — always available once the CLI is logged in on the host. V1: delivery roles only, not offered for Intake/Secretary."
            />
            <ModeButton
              icon={<Gem className="h-4 w-4" />}
              label="Gemini"
              description="Every agent uses Gemini (gemini-2.5-pro)."
              active={currentMode === "gemini"}
              onClick={flipToGemini}
              disabled={applyMode.isPending}
              labelHint="Gemini authenticates via a mounted ~/.gemini OAuth login (no API key) — always available once the CLI is logged in on the host. V1: delivery roles only, not offered for Intake/Secretary."
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
            <ModeButton
              icon={<Gauge className="h-4 w-4" />}
              label="Cost-Tiered"
              description="Seed developer:low → Haiku (see below)."
              active={false}
              onClick={flipToCostTiered}
              disabled={applyMode.isPending}
              labelHint="Unlike every button to the left this never wipes existing routing — it's a one-time additive seed you can re-run anytime. Edit or remove individual rows in the Complexity overrides section below."
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
          {currentMode === "codex" || currentMode === "mix" ? (
            <p className="text-xs text-muted-foreground">
              Codex agents run on OpenAI&apos;s official Codex CLI (ChatGPT
              subscription, mounted ~/.codex); the same command /
              secret-exfiltration guard, prompt-injection guard, and per-agent
              cost cap apply. V1: delivery roles only — not available for
              Intake/Secretary.
            </p>
          ) : null}
          {currentMode === "gemini" || currentMode === "mix" ? (
            <p className="text-xs text-muted-foreground">
              Gemini agents run on Google&apos;s official gemini CLI (OAuth
              login, mounted ~/.gemini); the same guards apply. V1: delivery
              roles only — not available for Intake/Secretary.
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

        {/* -------- Preset bar (compact, sits right above the per-agent table) -------- */}
        <Separator />
        <section className="space-y-2">
          <HelpTip label="A preset snapshots the ENTIRE current routing state — mode, every per-agent pin, every role row, and every complexity override — so you can switch between whole setups in one click instead of re-picking every Select.">
            <Label className="text-sm font-medium">Routing presets</Label>
          </HelpTip>
          <div className="flex flex-wrap items-center gap-2 rounded-md border border-dashed p-2">
            <Select value={selectedPresetId} onValueChange={setSelectedPresetId}>
              <SelectTrigger size="sm" className="w-48 text-xs">
                <SelectValue
                  placeholder={
                    presets.length ? "Choose a preset…" : "No presets saved"
                  }
                />
              </SelectTrigger>
              <SelectContent>
                {presets.map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    {p.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <HelpTip label="Replaces the ENTIRE current routing state with this preset's snapshot — every per-agent pin, role row, and global default, not merged with what's here now.">
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={handleApplyPreset}
                disabled={!selectedPresetId || applyPreset.isPending}
              >
                {applyPreset.isPending ? "Applying…" : "Apply"}
              </Button>
            </HelpTip>
            <HelpTip label="Deletes the saved snapshot only — has no effect on the routing currently applied.">
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={handleDeletePreset}
                disabled={!selectedPresetId || deletePreset.isPending}
              >
                Delete
              </Button>
            </HelpTip>
            <Separator orientation="vertical" className="h-6" />
            {showPresetNameInput ? (
              <>
                <Input
                  value={presetNameDraft}
                  onChange={(e) => setPresetNameDraft(e.target.value)}
                  placeholder="Preset name"
                  className="h-8 w-40 text-xs"
                />
                <Button
                  type="button"
                  size="sm"
                  onClick={handleSavePreset}
                  disabled={savePreset.isPending}
                >
                  {savePreset.isPending ? "Saving…" : "Confirm"}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setShowPresetNameInput(false);
                    setPresetNameDraft("");
                  }}
                >
                  Cancel
                </Button>
              </>
            ) : (
              <HelpTip label="Snapshots exactly what this card currently shows — the applied mode, every per-agent pin, and every complexity override.">
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => setShowPresetNameInput(true)}
                >
                  Save as preset…
                </Button>
              </HelpTip>
            )}
          </div>
        </section>

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
          {agentsError ? (
            <p className="flex items-center gap-1 rounded-md border p-4 text-xs text-amber-600">
              <AlertTriangle className="h-3 w-3 shrink-0" />
              Couldn&apos;t load the agent roster — per-agent overrides are
              unavailable until this reloads.
            </p>
          ) : agentsLoading ? (
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
              {agentGroups.map((group) => {
                const restrictInteractiveOnly =
                  group.title === INTERACTIVE_ONLY_GROUP_TITLE;
                return (
                  <div key={group.title} className="p-4">
                    <HelpTip label={group.titleHint}>
                      <h4 className="mb-2 w-fit text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                        {group.title}
                      </h4>
                    </HelpTip>
                    {restrictInteractiveOnly ? (
                      <p className="mb-2 text-[11px] text-muted-foreground">
                        Codex and Gemini are delivery-roles-only (V1) — not
                        offered here (no interactive Intake/Secretary support).
                      </p>
                    ) : null}
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
                              {renderMixSelectOptions(restrictInteractiveOnly)}
                            </SelectContent>
                          </Select>
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
          {catalogOllamaOnly.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              Ollama catalog empty — check /api/providers/catalog.
            </p>
          ) : null}
        </section>

        {/* -------- Complexity overrides (cost-tiered routing) -------- */}
        <Separator />
        <section className="space-y-3">
          <HelpTip label="Downgrade-only by policy: a role+complexity override can never point to a costlier tier than that role's baseline model — this lever only saves cost, it never spends more. Coordinator roles (cell_pm, main_pm), pr_reviewer, and board/CEO-facing roles aren't offered a row here at all; tier pinning for those is deliberate — cell_pm especially, since a coordinator is the last place to gamble a downgrade.">
            <Label className="text-sm font-medium">
              Complexity overrides
            </Label>
          </HelpTip>
          <p className="text-xs text-muted-foreground">
            Pin a role to a cheaper model for LOW- or HIGH-complexity tasks
            specifically — wins over that role&apos;s plain default and the
            global mode, still loses to a per-agent pin above. Leave a Select
            blank to remove the override. Coordinator roles (cell_pm, main_pm)
            aren&apos;t offered a row.
          </p>
          <div className="divide-y rounded-md border">
            {COMPLEXITY_OVERRIDE_ROLES.map((role) => (
              <div
                key={role}
                className="grid grid-cols-[1fr_140px_140px] items-center gap-4 p-3"
              >
                <HelpTip
                  label={`Applies only to ${COMPLEXITY_ROLE_LABELS[role]} agents; a per-agent pin above still wins over this.`}
                >
                  <div className="text-xs font-medium">
                    {COMPLEXITY_ROLE_LABELS[role]}
                  </div>
                </HelpTip>
                {(["low", "high"] as const).map((complexity) => (
                  <div
                    key={complexity}
                    className="space-y-1"
                    data-testid={`complexity-select-${role}-${complexity}`}
                  >
                    <HelpTip
                      label={`Model used for ${COMPLEXITY_ROLE_LABELS[role]} tasks a PM estimates as ${complexity.toUpperCase()} complexity. Must be no costlier than ${COMPLEXITY_ROLE_LABELS[role]}'s baseline model — the server rejects an upgrade attempt here.`}
                    >
                      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
                        {complexity}
                      </div>
                    </HelpTip>
                    <Select
                      value={complexityMap[role]?.[complexity] ?? "__clear__"}
                      onValueChange={(v: string) =>
                        handleComplexityChange(
                          role,
                          complexity,
                          v === "__clear__" ? "" : v,
                        )
                      }
                    >
                      <SelectTrigger size="sm" className="w-full text-xs">
                        <SelectValue placeholder="(none)" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="__clear__">(none)</SelectItem>
                        {catalogForMix.map(
                          (c: { model_name: string; display_name: string }) => (
                            <SelectItem key={c.model_name} value={c.model_name}>
                              {c.display_name}
                            </SelectItem>
                          ),
                        )}
                      </SelectContent>
                    </Select>
                  </div>
                ))}
              </div>
            ))}
          </div>
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
  variant:
    | "anthropic"
    | "grok"
    | "openai"
    | "gemini"
    | "ollama"
    | "self-hosted";
}) {
  const styles: Record<string, string> = {
    anthropic: "bg-blue-500/20 text-blue-700 dark:text-blue-400",
    ollama: "bg-violet-500/20 text-violet-700 dark:text-violet-400",
    "self-hosted": "bg-purple-500/20 text-purple-700 dark:text-purple-400",
    grok: "bg-teal-500/20 text-teal-700 dark:text-teal-400",
    openai: "bg-emerald-500/20 text-emerald-700 dark:text-emerald-400",
    gemini: "bg-sky-500/20 text-sky-700 dark:text-sky-400",
  };
  const labels: Record<string, string> = {
    anthropic: "A",
    ollama: "O",
    "self-hosted": "S",
    grok: "G",
    openai: "C",
    gemini: "Ge",
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
