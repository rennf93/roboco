"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { companyGoalsApi, type CompanyGoalsUpdate } from "@/lib/api/company-goals";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { Target, Save } from "lucide-react";
import { toast } from "sonner";

function parseError(e: unknown): string {
  return e instanceof Error ? e.message : "parse error";
}

export function CompanyGoalsCard() {
  const queryClient = useQueryClient();
  // null = "show the server value"; deriving the displayed value avoids syncing
  // query state into local state with an effect (react-hooks/set-state-in-effect).
  const [northStar, setNorthStar] = useState<string | null>(null);
  const [constraints, setConstraints] = useState<string | null>(null);
  const [objectives, setObjectives] = useState<string | null>(null);
  const [policy, setPolicy] = useState<string | null>(null);

  const { data: goals, isLoading } = useQuery({
    queryKey: ["company-goals"],
    queryFn: companyGoalsApi.get,
  });

  const northStarVal = northStar ?? (goals?.north_star ?? "");
  const constraintsVal = constraints ?? (goals?.constraints ?? []).join("\n");
  const objectivesVal =
    objectives ?? JSON.stringify(goals?.objectives ?? [], null, 2);
  const policyVal = policy ?? JSON.stringify(goals?.operating_policy ?? {}, null, 2);

  const saveMutation = useMutation({
    mutationFn: (update: CompanyGoalsUpdate) => companyGoalsApi.update(update),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["company-goals"] });
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
    let parsedObjectives: Record<string, unknown>[];
    let parsedPolicy: Record<string, unknown>;
    try {
      parsedObjectives = JSON.parse(objectivesVal);
      if (!Array.isArray(parsedObjectives)) {
        throw new Error("must be a JSON array");
      }
    } catch (e) {
      toast.error(`Objectives: invalid JSON — ${parseError(e)}`);
      return;
    }
    try {
      parsedPolicy = JSON.parse(policyVal);
      if (
        typeof parsedPolicy !== "object" ||
        parsedPolicy === null ||
        Array.isArray(parsedPolicy)
      ) {
        throw new Error("must be a JSON object");
      }
    } catch (e) {
      toast.error(`Operating policy: invalid JSON — ${parseError(e)}`);
      return;
    }
    saveMutation.mutate({
      north_star: northStarVal,
      objectives: parsedObjectives,
      constraints: constraintsVal
        .split("\n")
        .map((c) => c.trim())
        .filter(Boolean),
      operating_policy: parsedPolicy,
    });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Target className="h-5 w-5" />
          Company Charter
        </CardTitle>
        <CardDescription>
          The CEO-owned north star, objectives, constraints, and operating policy.
          Injected into every agent&apos;s briefing so all work stays goal-aware.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="north-star">North star</Label>
          <Textarea
            id="north-star"
            rows={3}
            value={northStarVal}
            disabled={isLoading}
            onChange={(e) => setNorthStar(e.target.value)}
            placeholder="The long-term vision in one or two sentences..."
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="constraints">Constraints (one per line)</Label>
          <Textarea
            id="constraints"
            rows={3}
            value={constraintsVal}
            disabled={isLoading}
            onChange={(e) => setConstraints(e.target.value)}
            placeholder={"AGPL only\nNo external data egress"}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="objectives">Objectives (JSON array)</Label>
          <Textarea
            id="objectives"
            rows={6}
            value={objectivesVal}
            disabled={isLoading}
            onChange={(e) => setObjectives(e.target.value)}
            className="font-mono text-xs"
            placeholder='[{"metric": "NPS", "target": 50, "status": "active"}]'
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="policy">Operating policy (JSON object)</Label>
          <Textarea
            id="policy"
            rows={6}
            value={policyVal}
            disabled={isLoading}
            onChange={(e) => setPolicy(e.target.value)}
            className="font-mono text-xs"
            placeholder='{"autonomy_level": "assisted", "monthly_budget_cap": 500}'
          />
        </div>
        <Button onClick={handleSave} disabled={saveMutation.isPending || isLoading}>
          <Save className="h-4 w-4 mr-2" />
          {saveMutation.isPending ? "Saving..." : "Save charter"}
        </Button>
      </CardContent>
    </Card>
  );
}
