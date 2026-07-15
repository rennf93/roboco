"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  conventionsApi,
  type ConventionsActionResult,
  type ConventionsCustomRule,
  type ConventionsModule,
  type ConventionsStandard,
  type ConventionsWaiver,
  type DefinitionKind,
  type RuleLevel,
} from "@/lib/api/conventions";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { HelpTip } from "@/components/ui/help-tip";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { toast } from "sonner";

const FORBIDDABLE_KINDS: DefinitionKind[] = [
  "model",
  "route",
  "helper",
  "business_logic",
  "component",
];

const KIND_HINTS: Record<DefinitionKind, string> = {
  model: "Pydantic / ORM data models",
  route: "FastAPI route handlers",
  helper: "Standalone top-level functions — placement here only warns, never blocks",
  business_logic: "Service-layer logic (the classes services/ owns)",
  component: "Frontend components (.tsx)",
  other: "Anything the classifier can't place in the kinds above",
};

function actionToast(verb: string, result: ConventionsActionResult): void {
  if (result.created && result.pr_number != null) {
    toast.success(
      `${verb}: opened PR #${result.pr_number} on ${result.branch}`,
    );
  } else {
    toast.success(
      `${verb}: prepared on ${result.branch} (no remote PR — workspace not cloned)`,
    );
  }
}

export function ConventionsTab({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<ConventionsStandard | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["conventions", projectId],
    queryFn: () => conventionsApi.get(projectId),
  });

  const { data: findings } = useQuery({
    queryKey: ["conventions-findings", projectId],
    queryFn: () => conventionsApi.findings(projectId),
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["conventions", projectId] });

  const save = useMutation({
    mutationFn: (standard: ConventionsStandard) =>
      conventionsApi.update(projectId, standard),
    onSuccess: (result) => {
      actionToast("Saved", result);
      setDraft(null);
      invalidate();
    },
    onError: (error) =>
      toast.error(
        `Save failed: ${error instanceof Error ? error.message : "unknown error"}`,
      ),
  });

  const restore = useMutation({
    mutationFn: () => conventionsApi.restore(projectId),
    onSuccess: (result) => {
      actionToast("Restore", result);
      invalidate();
    },
    onError: (error) =>
      toast.error(
        `Restore failed: ${error instanceof Error ? error.message : "unknown error"}`,
      ),
  });

  if (isLoading) {
    return (
      <p className="py-4 text-sm text-muted-foreground">Loading conventions…</p>
    );
  }
  const standard = draft ?? data?.standard ?? null;
  if (!standard || !data) {
    return (
      <p className="py-4 text-sm text-muted-foreground">
        No conventions available for this project.
      </p>
    );
  }

  const edit = (next: Partial<ConventionsStandard>) =>
    setDraft({ ...standard, ...next });

  const setRuleLevel = (name: string, level: RuleLevel) =>
    edit({ rules: { ...standard.rules, [name]: { name, level } } });

  const updateModule = (index: number, next: Partial<ConventionsModule>) =>
    edit({
      modules: standard.modules.map((m, i) =>
        i === index ? { ...m, ...next } : m,
      ),
    });
  const addModule = () =>
    edit({
      modules: [...standard.modules, { path: "", purpose: "", forbidden: [] }],
    });
  const removeModule = (index: number) =>
    edit({ modules: standard.modules.filter((_, i) => i !== index) });
  const toggleForbidden = (index: number, kind: DefinitionKind) => {
    const current = standard.modules[index].forbidden;
    const forbidden = current.includes(kind)
      ? current.filter((k) => k !== kind)
      : [...current, kind];
    updateModule(index, { forbidden });
  };

  const updateCustom = (index: number, next: Partial<ConventionsCustomRule>) =>
    edit({
      custom: standard.custom.map((c, i) =>
        i === index ? { ...c, ...next } : c,
      ),
    });
  const addCustom = () =>
    edit({
      custom: [
        ...standard.custom,
        { id: "", pattern: "", message: "", level: "warn", languages: [] },
      ],
    });
  const removeCustom = (index: number) =>
    edit({ custom: standard.custom.filter((_, i) => i !== index) });

  const updateWaiver = (index: number, next: Partial<ConventionsWaiver>) =>
    edit({
      waivers: standard.waivers.map((w, i) =>
        i === index ? { ...w, ...next } : w,
      ),
    });
  const addWaiver = () =>
    edit({
      waivers: [...standard.waivers, { path: "", rule: "", reason: "" }],
    });
  const removeWaiver = (index: number) =>
    edit({ waivers: standard.waivers.filter((_, i) => i !== index) });

  const status = data.health.status;
  // "degraded" is the only problem state: a committed file that won't parse.
  // "missing"/"unknown" is the normal starting point — no file yet, defaults
  // apply and are already enforced — so it must not be flagged as an error.
  const degraded = status === "degraded";
  const usingDefaults = status === "missing" || status === "unknown";

  const moduleBoundaries = (
    <Card className="lg:flex lg:flex-col">
      <CardHeader>
        <HelpTip label="The effective map: auto-derived defaults from a scan of this repo, overlaid by any committed .roboco/conventions.yml. Editing here and saving writes it back as that committed file.">
          <CardTitle className="text-sm w-fit">Module boundaries</CardTitle>
        </HelpTip>
        <CardDescription>
          Which definition kinds are forbidden in each module. Click a kind to
          toggle it.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 lg:flex lg:min-h-0 lg:flex-1 lg:flex-col">
        <div className="space-y-3 lg:min-h-0 lg:flex-1 lg:overflow-y-auto lg:pr-1">
          {standard.modules.length === 0 && (
            <p className="text-sm text-muted-foreground">
              No modules mapped yet.
            </p>
          )}
          {standard.modules.map((module, index) => (
            <div
              key={index}
              className="space-y-2 rounded-md border border-border p-3"
            >
              <div className="flex items-center gap-2">
                <HelpTip label="Repo-relative directory this rule applies to (e.g. roboco/services)">
                  <Input
                    value={module.path}
                    placeholder="path/to/module"
                    onChange={(e) =>
                      updateModule(index, { path: e.target.value })
                    }
                  />
                </HelpTip>
                <HelpTip label="Deletes this module row locally — nothing changes until Save to repo">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => removeModule(index)}
                  >
                    Remove
                  </Button>
                </HelpTip>
              </div>
              <HelpTip label="One-line description shown in the ambient 'Architectural Standard' prompt block every agent sees">
                <Input
                  value={module.purpose}
                  placeholder="what this module is for"
                  onChange={(e) =>
                    updateModule(index, { purpose: e.target.value })
                  }
                />
              </HelpTip>
              <div className="flex flex-wrap gap-1">
                {FORBIDDABLE_KINDS.map((kind) => (
                  <HelpTip key={kind} label={KIND_HINTS[kind]}>
                    <Badge
                      variant={
                        module.forbidden.includes(kind)
                          ? "destructive"
                          : "outline"
                      }
                      className="cursor-pointer"
                      onClick={() => toggleForbidden(index, kind)}
                    >
                      no {kind}
                    </Badge>
                  </HelpTip>
                ))}
              </div>
            </div>
          ))}
        </div>
        <HelpTip label="Adds a blank module row to constrain another directory">
          <Button variant="outline" size="sm" onClick={addModule}>
            Add module
          </Button>
        </HelpTip>
      </CardContent>
    </Card>
  );

  const rules = (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Rules</CardTitle>
        <CardDescription>
          Toggle a rule between warn (advisory) and block (refuses the gate).
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {Object.values(standard.rules).map((rule) => (
          <div
            key={rule.name}
            className="flex items-center justify-between gap-4"
          >
            <span className="text-sm">{rule.name.replace(/_/g, " ")}</span>
            <div className="flex items-center gap-2">
              <HelpTip
                label={
                  rule.level === "block"
                    ? "Block: refuses i_am_done / pr_pass with the offending file:line"
                    : "Warn: informational only, never blocks the gate"
                }
              >
                <span className="w-10 text-right text-xs text-muted-foreground">
                  {rule.level}
                </span>
              </HelpTip>
              <Switch
                checked={rule.level === "block"}
                onCheckedChange={(checked) =>
                  setRuleLevel(rule.name, checked ? "block" : "warn")
                }
              />
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  );

  const waivers = (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Waivers</CardTitle>
        <CardDescription>
          Accountable escapes — exempt a file from a rule with a reason
          (reviewed in the PR, never a silent in-code suppression).
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {standard.waivers.map((waiver, index) => (
          <div
            key={index}
            className="space-y-2 rounded-md border border-border p-3"
          >
            <div className="flex items-center gap-2">
              <HelpTip label="Repo-relative path or glob this waiver exempts from the rule on the right">
                <Input
                  value={waiver.path}
                  placeholder="path/to/file.py"
                  onChange={(e) =>
                    updateWaiver(index, { path: e.target.value })
                  }
                />
              </HelpTip>
              <HelpTip label="Must match a rule name above exactly for the waiver to apply">
                <Input
                  value={waiver.rule}
                  placeholder="rule name"
                  onChange={(e) =>
                    updateWaiver(index, { rule: e.target.value })
                  }
                />
              </HelpTip>
              <HelpTip label="Deletes this waiver row locally — nothing changes until Save to repo">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => removeWaiver(index)}
                >
                  Remove
                </Button>
              </HelpTip>
            </div>
            <HelpTip label="Accountability note — committed with the waiver and reviewed in the PR, never a silent suppression">
              <Input
                value={waiver.reason}
                placeholder="why this is waived"
                onChange={(e) =>
                  updateWaiver(index, { reason: e.target.value })
                }
              />
            </HelpTip>
          </div>
        ))}
        <HelpTip label="Adds a blank waiver row">
          <Button variant="outline" size="sm" onClick={addWaiver}>
            Add waiver
          </Button>
        </HelpTip>
      </CardContent>
    </Card>
  );

  const customRules = (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Custom rules</CardTitle>
        <CardDescription>
          Project-specific regex rules — a pattern, a message, and a level.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {standard.custom.map((rule, index) => (
          <div
            key={index}
            className="space-y-2 rounded-md border border-border p-3"
          >
            <div className="flex items-center gap-2">
              <HelpTip label="A short, unique identifier for this rule (shown as its badge in Recent violations)">
                <Input
                  value={rule.id}
                  placeholder="rule-id"
                  onChange={(e) => updateCustom(index, { id: e.target.value })}
                />
              </HelpTip>
              <HelpTip
                label={
                  rule.level === "block"
                    ? "Block: a match refuses the conventions gate"
                    : "Warn: a match is advisory only, never blocks the gate"
                }
              >
                <span className="w-10 text-right text-xs text-muted-foreground">
                  {rule.level}
                </span>
              </HelpTip>
              <Switch
                checked={rule.level === "block"}
                onCheckedChange={(checked) =>
                  updateCustom(index, { level: checked ? "block" : "warn" })
                }
              />
              <HelpTip label="Deletes this custom rule row locally — nothing changes until Save to repo">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => removeCustom(index)}
                >
                  Remove
                </Button>
              </HelpTip>
            </div>
            <HelpTip label="Regex tested against each changed file's source text — a bad pattern is skipped, never fails the gate">
              <Input
                value={rule.pattern}
                placeholder="regex pattern"
                onChange={(e) =>
                  updateCustom(index, { pattern: e.target.value })
                }
              />
            </HelpTip>
            <HelpTip label="Shown next to a match in Recent violations below">
              <Input
                value={rule.message}
                placeholder="message shown when it matches"
                onChange={(e) =>
                  updateCustom(index, { message: e.target.value })
                }
              />
            </HelpTip>
          </div>
        ))}
        <HelpTip label="Adds a blank custom regex rule">
          <Button variant="outline" size="sm" onClick={addCustom}>
            Add custom rule
          </Button>
        </HelpTip>
      </CardContent>
    </Card>
  );

  const recentViolations = (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Recent violations</CardTitle>
        <CardDescription>
          The latest findings recorded across this project&apos;s tasks.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        {(!findings || findings.length === 0) && (
          <p className="text-sm text-muted-foreground">
            No violations recorded yet.
          </p>
        )}
        {(findings ?? []).map((finding, index) => (
          <div
            key={`${finding.file}:${finding.line}:${finding.rule}:${index}`}
            className="flex items-start justify-between gap-4 text-sm"
          >
            <div className="min-w-0">
              <code>
                {finding.file}:{finding.line}
              </code>{" "}
              <span className="text-muted-foreground">{finding.message}</span>
            </div>
            <HelpTip
              label={
                finding.level === "block"
                  ? "Block-level: refuses the gate"
                  : "Warning: advisory only"
              }
            >
              <Badge
                variant={finding.level === "block" ? "destructive" : "secondary"}
              >
                {finding.rule}
              </Badge>
            </HelpTip>
          </div>
        ))}
      </CardContent>
    </Card>
  );

  return (
    <div className="space-y-4 py-2">
      {degraded && (
        <Card className="border-amber-500/40">
          <CardHeader>
            <CardTitle className="text-sm">
              Conventions degraded — committed file unparseable
            </CardTitle>
            <CardDescription>
              The committed <code>.roboco/conventions.yml</code> could not be
              parsed; the effective map fell back to the last-good cache plus
              auto-derived defaults. Restore re-commits the last-good file.
            </CardDescription>
          </CardHeader>
        </Card>
      )}
      {usingDefaults && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">
              Using auto-derived defaults
            </CardTitle>
            <CardDescription>
              No <code>.roboco/conventions.yml</code> is committed yet. These
              rules are auto-derived from the repository and are already
              enforced. Edit them below and Save to repo to make them canonical.
            </CardDescription>
          </CardHeader>
        </Card>
      )}

      {/* Two columns on wide viewports so the modal isn't a long single column;
          Module boundaries | Rules, then Waivers | Custom rules. items-stretch
          makes each row's two cards equal height; Module boundaries scrolls
          internally (below) so it matches Rules instead of running long. Stacks
          to one column on mobile. */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 lg:items-stretch">
        {moduleBoundaries}
        {rules}
        {waivers}
        {customRules}
      </div>

      {/* Recent violations spans the full width on its own row. */}
      {recentViolations}

      <div className="flex justify-between">
        <HelpTip label={restore.isPending ? "Restoring…" : null}>
          <Button
            variant="outline"
            size="sm"
            disabled={restore.isPending}
            onClick={() => restore.mutate()}
          >
            Restore from last-good
          </Button>
        </HelpTip>
        <HelpTip
          label={
            save.isPending
              ? "Saving…"
              : draft == null && !usingDefaults
                ? "Edit a module, rule, waiver, or custom rule above to enable saving."
                : null
          }
        >
          <Button
            size="sm"
            disabled={(draft == null && !usingDefaults) || save.isPending}
            onClick={() => save.mutate(draft ?? standard)}
          >
            {usingDefaults && draft == null
              ? "Save defaults to repo"
              : "Save to repo"}
          </Button>
        </HelpTip>
      </div>
    </div>
  );
}
