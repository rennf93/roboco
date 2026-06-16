"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Save } from "lucide-react";
import {
  companyGoalsApi,
  type CompanyGoals,
  type CompanyGoalsUpdate,
} from "@/lib/api/company-goals";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { OfflineState } from "@/components/ui/offline-state";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function parseError(e: unknown): string {
  return e instanceof Error ? e.message : "Unknown error";
}

/** Format an ISO timestamp nicely, or return the raw string. */
function formatTs(ts: string | null | undefined): string {
  if (!ts) return "—";
  try {
    return new Date(ts).toLocaleString();
  } catch {
    return ts;
  }
}

// ---------------------------------------------------------------------------
// Objectives field — one text field per key derived from the first item.
// Falls back to a single "value" field when array is empty.
// ---------------------------------------------------------------------------

interface ObjectivesEditorProps {
  items: Record<string, unknown>[];
  onChange: (items: Record<string, unknown>[]) => void;
  disabled: boolean;
}

function ObjectivesEditor({ items, onChange, disabled }: ObjectivesEditorProps) {
  // Derive keys from the first item; fall back to generic keys.
  const keys =
    items.length > 0
      ? Object.keys(items[0])
      : ["metric", "target", "status"];

  const handleChange = (
    rowIdx: number,
    key: string,
    value: string
  ) => {
    const next = items.map((item, i) =>
      i === rowIdx ? { ...item, [key]: value } : item
    );
    onChange(next);
  };

  const addRow = () => {
    const empty: Record<string, unknown> = {};
    keys.forEach((k) => (empty[k] = ""));
    onChange([...items, empty]);
  };

  const removeRow = (idx: number) => {
    onChange(items.filter((_, i) => i !== idx));
  };

  return (
    <div className="space-y-3">
      {items.map((item, rowIdx) => (
        <div key={rowIdx} className="rounded-lg border p-3 space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-muted-foreground">
              Objective #{rowIdx + 1}
            </span>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={disabled}
              onClick={() => removeRow(rowIdx)}
              className="h-6 px-2 text-xs text-destructive hover:text-destructive"
            >
              Remove
            </Button>
          </div>
          {keys.map((key) => (
            <div key={key} className="space-y-1">
              <Label htmlFor={`obj-${rowIdx}-${key}`} className="text-xs capitalize">
                {key.replace(/_/g, " ")}
              </Label>
              <Input
                id={`obj-${rowIdx}-${key}`}
                value={String(item[key] ?? "")}
                disabled={disabled}
                onChange={(e) => handleChange(rowIdx, key, e.target.value)}
                className="h-8 text-sm"
              />
            </div>
          ))}
        </div>
      ))}
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={disabled}
        onClick={addRow}
      >
        + Add objective
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Operating policy editor — one input per key
// ---------------------------------------------------------------------------

interface PolicyEditorProps {
  policy: Record<string, unknown>;
  onChange: (policy: Record<string, unknown>) => void;
  disabled: boolean;
}

function PolicyEditor({ policy, onChange, disabled }: PolicyEditorProps) {
  const keys = Object.keys(policy);

  const handleChange = (key: string, value: string) => {
    onChange({ ...policy, [key]: value });
  };

  return (
    <div className="space-y-2">
      {keys.length === 0 && (
        <p className="text-xs text-muted-foreground">
          No policy keys yet — save with the backend to populate.
        </p>
      )}
      {keys.map((key) => (
        <div key={key} className="space-y-1">
          <Label htmlFor={`policy-${key}`} className="text-xs capitalize">
            {key.replace(/_/g, " ")}
          </Label>
          <Input
            id={`policy-${key}`}
            value={String(policy[key] ?? "")}
            disabled={disabled}
            onChange={(e) => handleChange(key, e.target.value)}
            className="h-8 text-sm"
          />
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Skeleton loading state
// ---------------------------------------------------------------------------

function GoalsTabSkeleton() {
  return (
    <Card>
      <CardHeader>
        <Skeleton className="h-6 w-40 mb-1" />
        <Skeleton className="h-4 w-80" />
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Skeleton className="h-4 w-24" />
          <Skeleton className="h-20 w-full" />
        </div>
        <div className="space-y-2">
          <Skeleton className="h-4 w-32" />
          <Skeleton className="h-20 w-full" />
        </div>
        <div className="space-y-3">
          <Skeleton className="h-4 w-24" />
          <div className="rounded-lg border p-3 space-y-2">
            <Skeleton className="h-4 w-24" />
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-4 w-16" />
            <Skeleton className="h-8 w-full" />
          </div>
        </div>
        <div className="space-y-2">
          <Skeleton className="h-4 w-36" />
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-full" />
        </div>
        <Skeleton className="h-9 w-32" />
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Main editable form — only rendered when data is loaded
// ---------------------------------------------------------------------------

interface GoalsFormProps {
  goals: CompanyGoals;
  refetch: () => void;
}

function GoalsForm({ goals, refetch }: GoalsFormProps) {
  const queryClient = useQueryClient();

  const [northStar, setNorthStar] = useState<string | null>(null);
  const [constraints, setConstraints] = useState<string | null>(null);
  const [objectives, setObjectives] = useState<Record<string, unknown>[] | null>(null);
  const [policy, setPolicy] = useState<Record<string, unknown> | null>(null);

  const northStarVal = northStar ?? goals.north_star ?? "";
  const constraintsVal = constraints ?? (goals.constraints ?? []).join("\n");
  const objectivesVal = objectives ?? (goals.objectives ?? []);
  const policyVal = policy ?? (goals.operating_policy ?? {});

  const saveMutation = useMutation({
    mutationFn: (update: CompanyGoalsUpdate) => companyGoalsApi.update(update),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["company-goals"] });
      setNorthStar(null);
      setConstraints(null);
      setObjectives(null);
      setPolicy(null);
      toast.success("Company charter updated");
    },
    onError: (error) => {
      toast.error(`Failed to save: ${parseError(error)}`);
    },
  });

  const handleSave = () => {
    saveMutation.mutate({
      north_star: northStarVal,
      objectives: objectivesVal,
      constraints: constraintsVal
        .split("\n")
        .map((c) => c.trim())
        .filter(Boolean),
      operating_policy: policyVal,
    });
  };

  const saving = saveMutation.isPending;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Company Charter</CardTitle>
        <CardDescription>
          CEO-owned north star, objectives, constraints, and operating policy.
          Injected into every agent&apos;s briefing so all work stays goal-aware.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {/* North star */}
        <div className="space-y-2">
          <Label htmlFor="north-star">North star</Label>
          <Textarea
            id="north-star"
            rows={3}
            value={northStarVal}
            disabled={saving}
            onChange={(e) => setNorthStar(e.target.value)}
            placeholder="The long-term vision in one or two sentences…"
          />
        </div>

        {/* Constraints */}
        <div className="space-y-2">
          <Label htmlFor="constraints">Constraints (one per line)</Label>
          <Textarea
            id="constraints"
            rows={3}
            value={constraintsVal}
            disabled={saving}
            onChange={(e) => setConstraints(e.target.value)}
            placeholder={"AGPL only\nNo external data egress"}
          />
        </div>

        {/* Objectives */}
        <div className="space-y-2">
          <Label>Objectives</Label>
          <ObjectivesEditor
            items={objectivesVal}
            onChange={(items) => setObjectives(items)}
            disabled={saving}
          />
        </div>

        {/* Operating policy */}
        <div className="space-y-2">
          <Label>Operating policy</Label>
          <PolicyEditor
            policy={policyVal}
            onChange={(p) => setPolicy(p)}
            disabled={saving}
          />
        </div>

        {/* Save */}
        <Button onClick={handleSave} disabled={saving}>
          <Save className="h-4 w-4 mr-2" />
          {saving ? "Saving…" : "Save charter"}
        </Button>

        {/* Metadata */}
        <div className="border-t pt-4 space-y-1 text-xs text-muted-foreground">
          <p>
            Last updated:{" "}
            <span className="font-medium text-foreground">
              {formatTs(goals.updated_at)}
            </span>
          </p>
          {goals.updated_by && (
            <p>
              Updated by:{" "}
              <span className="font-medium text-foreground font-mono">
                {goals.updated_by}
              </span>
            </p>
          )}
        </div>

        {/* Hidden — just to make the refetch prop used */}
        <button type="button" className="hidden" onClick={refetch} />
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Public export
// ---------------------------------------------------------------------------

export function GoalsTab() {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["company-goals"],
    queryFn: companyGoalsApi.get,
  });

  if (isLoading) return <GoalsTabSkeleton />;

  if (isError || !data) {
    return (
      <OfflineState
        title="Failed to load company goals"
        description="Could not reach the orchestrator API. Check the backend is running."
        onRetry={() => void refetch()}
      />
    );
  }

  return <GoalsForm goals={data} refetch={() => void refetch()} />;
}
