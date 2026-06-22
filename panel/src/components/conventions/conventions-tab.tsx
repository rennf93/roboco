"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  conventionsApi,
  type ConventionsActionResult,
  type ConventionsStandard,
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
import { Switch } from "@/components/ui/switch";
import { toast } from "sonner";

function actionToast(verb: string, result: ConventionsActionResult): void {
  if (result.created && result.pr_number != null) {
    toast.success(`${verb}: opened PR #${result.pr_number} on ${result.branch}`);
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

  const setRuleLevel = (name: string, level: RuleLevel) =>
    setDraft({
      ...standard,
      rules: { ...standard.rules, [name]: { name, level } },
    });

  const status = data.health.status;
  // "degraded" is the only problem state: a committed file that won't parse.
  // "missing"/"unknown" is the normal starting point — no file yet, defaults
  // apply and are already enforced — so it must not be flagged as an error.
  const degraded = status === "degraded";
  const usingDefaults = status === "missing" || status === "unknown";

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
            <CardTitle className="text-sm">Using auto-derived defaults</CardTitle>
            <CardDescription>
              No <code>.roboco/conventions.yml</code> is committed yet. These
              rules are auto-derived from the repository and are already
              enforced. Edit them below and Save to repo to make them canonical.
            </CardDescription>
          </CardHeader>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Module boundaries</CardTitle>
          <CardDescription>
            Which definition kinds are forbidden in each module.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          {standard.modules.length === 0 && (
            <p className="text-sm text-muted-foreground">No modules mapped yet.</p>
          )}
          {standard.modules.map((module) => (
            <div
              key={module.path}
              className="flex items-start justify-between gap-4 text-sm"
            >
              <div className="min-w-0">
                <code>{module.path}</code>{" "}
                <span className="text-muted-foreground">— {module.purpose}</span>
              </div>
              <div className="flex flex-wrap justify-end gap-1">
                {module.forbidden.map((kind) => (
                  <Badge key={kind} variant="secondary">
                    no {kind}
                  </Badge>
                ))}
              </div>
            </div>
          ))}
        </CardContent>
      </Card>

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
                <span className="w-10 text-right text-xs text-muted-foreground">
                  {rule.level}
                </span>
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
              <Badge variant={finding.level === "block" ? "destructive" : "secondary"}>
                {finding.rule}
              </Badge>
            </div>
          ))}
        </CardContent>
      </Card>

      <div className="flex justify-between">
        <Button
          variant="outline"
          size="sm"
          disabled={restore.isPending}
          onClick={() => restore.mutate()}
        >
          Restore from last-good
        </Button>
        <Button
          size="sm"
          disabled={(draft == null && !usingDefaults) || save.isPending}
          onClick={() => save.mutate(draft ?? standard)}
        >
          {usingDefaults && draft == null ? "Save defaults to repo" : "Save to repo"}
        </Button>
      </div>
    </div>
  );
}
