"use client";

import { useEffect, useState } from "react";
import { Loader2, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useProjects } from "@/hooks/use-projects";
import { useProducts } from "@/hooks/use-products";
import type { TargetKind } from "@/hooks/use-prompter";

interface IntakeFormProps {
  targetKind: TargetKind;
  onTargetKind: (k: TargetKind) => void;
  projectId: string;
  onProjectId: (id: string) => void;
  productId: string;
  onProductId: (id: string) => void;
  initialMessage: string;
  onInitialMessage: (v: string) => void;
  isValid: boolean;
  isPreparing: boolean;
  onStart: () => void;
}

/**
 * The one-time scope form shown before the chat. The agent is spawned against
 * exactly one of project / product, clones that scope's repo(s), and reads the
 * real code before answering — so the scope must be chosen up front.
 */
export function IntakeForm({
  targetKind,
  onTargetKind,
  projectId,
  onProjectId,
  productId,
  onProductId,
  initialMessage,
  onInitialMessage,
  isValid,
  isPreparing,
  onStart,
}: IntakeFormProps) {
  const { data: projects = [] } = useProjects();
  const { data: products = [] } = useProducts();

  // The first clone of a repo can take a few minutes; without feedback the
  // "Preparing…" button looks frozen. Tick an elapsed timer while preparing
  // and drive a saturating progress bar + staged copy so the wait reads as
  // work-in-progress, not a hang. The bar approaches but never reaches 100%
  // until the agent actually answers (which flips isPreparing off).
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!isPreparing) return;
    const id = setInterval(() => setElapsed((s) => s + 1), 1000);
    // Reset on cleanup (when preparing ends or the component unmounts) rather
    // than synchronously in the effect body, which would trigger a cascading
    // render.
    return () => {
      clearInterval(id);
      setElapsed(0);
    };
  }, [isPreparing]);

  const prepPct = Math.min(95, Math.round(100 * (1 - Math.exp(-elapsed / 75))));
  const prepStage =
    elapsed < 15
      ? "Spinning up the agent…"
      : elapsed < 45
        ? "Cloning your repository…"
        : elapsed < 120
          ? "First clone can take a couple of minutes — hang tight…"
          : "Reading the codebase…";
  const prepElapsed = `${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, "0")}`;

  return (
    <div className="flex flex-1 items-center justify-center px-6 py-8">
      <div className="w-full max-w-lg space-y-6 rounded-xl border bg-card p-6 shadow-sm">
        <div className="flex items-start gap-3">
          <Sparkles className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
          <div>
            <h2 className="text-base font-semibold">Start an intake chat</h2>
            <p className="text-sm text-muted-foreground">
              Pick what you&apos;re working on. An agent reads that code, then
              interviews you and drafts the task.
            </p>
          </div>
        </div>

        {/* Scope: single-cell project vs board-led product */}
        <div className="space-y-2">
          <Label>
            Scope <span className="text-destructive">*</span>
          </Label>
          <Tabs
            value={targetKind}
            onValueChange={(v) => onTargetKind(v as TargetKind)}
          >
            <TabsList className="grid w-full grid-cols-2">
              <TabsTrigger value="project" disabled={isPreparing}>
                Single cell (Project)
              </TabsTrigger>
              <TabsTrigger value="product" disabled={isPreparing}>
                Board-led (Product)
              </TabsTrigger>
            </TabsList>
          </Tabs>

          {targetKind === "project" ? (
            <Select
              value={projectId}
              onValueChange={onProjectId}
              disabled={isPreparing}
            >
              <SelectTrigger>
                <SelectValue placeholder="Select a project…" />
              </SelectTrigger>
              <SelectContent>
                {projects.map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    {p.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <>
              <Select
                value={productId}
                onValueChange={onProductId}
                disabled={isPreparing}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select a product…" />
                </SelectTrigger>
                <SelectContent>
                  {products.map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.name} ({p.cell_count} cells)
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {products.length === 0 && (
                <p className="text-xs text-muted-foreground">
                  No products exist yet. A board-led feature needs a product (a
                  cell→repo map) — create one under Products, or target a single
                  project instead.
                </p>
              )}
            </>
          )}
        </div>

        {/* Opening message */}
        <div className="space-y-1.5">
          <Label htmlFor="intake-initial-message">
            What do you want to build?{" "}
            <span className="text-destructive">*</span>
          </Label>
          <Textarea
            id="intake-initial-message"
            value={initialMessage}
            onChange={(e) => onInitialMessage(e.target.value)}
            placeholder="Describe the idea. The agent will read the code and ask sharp follow-ups…"
            rows={4}
            disabled={isPreparing}
          />
        </div>

        <Button
          className="w-full"
          onClick={onStart}
          disabled={!isValid || isPreparing}
        >
          {isPreparing ? (
            <>
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              Preparing the agent…
            </>
          ) : (
            "Start chatting"
          )}
        </Button>

        {isPreparing && (
          <div className="space-y-1.5" aria-live="polite">
            <Progress value={prepPct} />
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>{prepStage}</span>
              <span className="tabular-nums">{prepElapsed}</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
