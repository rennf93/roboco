"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { settingsApi } from "@/lib/api";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { HelpTip } from "@/components/ui/help-tip";
import { Scale } from "lucide-react";
import { toast } from "sonner";

// Dotted tri-state settings keys (off | shadow | on), validated server-side by
// _validate_pilot_mode in roboco/services/settings.py. Keep the slug list in
// sync with that registry and with roboco/services/decisions/pilots.py.
export const DECISIONS_PILOTS: {
  slug: string;
  label: string;
  description: string;
}[] = [
  {
    slug: "self_heal",
    label: "Self-heal (transient-CI gate)",
    description:
      "Ask for a verdict on whether a CI failure is transient before a fix task opens.",
  },
  {
    slug: "parking",
    label: "Parking (rate-limit routing)",
    description:
      "Pick whether a rate-limited agent parks or re-routes when a provider limit hits.",
  },
  {
    slug: "complexity",
    label: "Complexity (spawn-time routing)",
    description:
      "Score a task's complexity at spawn to help pick the model tier it runs on.",
  },
  {
    slug: "preflight_diff",
    label: "Preflight diff (agent lane)",
    description:
      "Pre-submit self-check: the developer gets a verdict over its own diff before i_am_done.",
  },
  {
    slug: "triage_failure",
    label: "Triage failure (agent lane)",
    description:
      "Red-test triage: classify a failing test run as transient or real before a QA bounce.",
  },
  {
    slug: "steer_gate",
    label: "Steer gate (A2A steering)",
    description:
      "Vet inbound A2A steering messages so a peer agent cannot derail a task mid-flight.",
  },
  {
    slug: "transcript_notes",
    label: "Transcript notes (at finalize)",
    description:
      "Transcript auto-notes: distill the transcript into notes when the task finalizes.",
  },
];

export const PILOT_MODES = ["off", "shadow", "on"] as const;
export type PilotMode = (typeof PILOT_MODES)[number];

export function pilotSettingKey(slug: string): string {
  return `decisions.pilot.${slug}`;
}

// One row per pilot: label + description on the left, the tri-state Select on
// the right. Shadow means the verdict is computed and logged but nothing acts
// on it; on means the verdict is acted on; both persist immediately but only
// take effect on the next backend restart.
function PilotRow({
  slug,
  label,
  description,
  value,
  onChange,
  pending,
}: {
  slug: string;
  label: string;
  description: string;
  value: PilotMode;
  onChange: (mode: PilotMode) => void;
  pending: boolean;
}) {
  const key = pilotSettingKey(slug);
  return (
    <div
      data-testid={`pilot-row-${slug}`}
      className="flex items-start justify-between gap-4 rounded-lg border p-4"
    >
      <div className="min-w-0">
        <HelpTip label={`Setting key: ${key}`}>
          <Label htmlFor={`pilot-${slug}`}>{label}</Label>
        </HelpTip>
        <p className="text-sm text-muted-foreground">{description}</p>
      </div>
      <Select
        value={value}
        onValueChange={(v) => onChange(v as PilotMode)}
        disabled={pending}
      >
        <SelectTrigger
          id={`pilot-${slug}`}
          aria-label={`${label} mode`}
          className="w-32 shrink-0"
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="off">off</SelectItem>
          <SelectItem value="shadow">shadow</SelectItem>
          <SelectItem value="on">on</SelectItem>
        </SelectContent>
      </Select>
    </div>
  );
}

export function DecisionsPilotsCard() {
  const queryClient = useQueryClient();

  const { data: settings, isLoading } = useQuery({
    queryKey: ["settings"],
    queryFn: settingsApi.getAll,
  });

  const setModeMutation = useMutation({
    mutationFn: ({ slug, mode }: { slug: string; mode: PilotMode }) =>
      settingsApi.update(pilotSettingKey(slug), mode),
    onSuccess: (_data, { slug, mode }) => {
      queryClient.invalidateQueries({ queryKey: ["settings"] });
      toast.success(
        `Pilot ${slug} set to ${mode} - takes effect on next restart`,
      );
    },
    onError: (error) => {
      toast.error(
        `Failed to update: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    },
  });

  // An unset row means off, which is exactly the pre-Decisions behavior for
  // that pilot (the backend treats a missing key the same way).
  const storedModes = settings ?? {};

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Scale className="h-5 w-5" />
          Decisions Pilots
        </CardTitle>
        <CardDescription>
          Per-pilot mode for the Decisions service. &quot;shadow&quot; logs
          verdicts without acting on them; &quot;on&quot; acts on them. Changes
          persist immediately and take effect on the next backend restart.
          Needs the Decisions master flag on (Feature Flags below).
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading && (
          <p className="text-sm text-muted-foreground">Loading pilots…</p>
        )}
        <div className="space-y-3">
          {DECISIONS_PILOTS.map((pilot) => (
            <PilotRow
              key={pilot.slug}
              slug={pilot.slug}
              label={pilot.label}
              description={pilot.description}
              value={
                (storedModes[pilotSettingKey(pilot.slug)] as PilotMode) ?? "off"
              }
              onChange={(mode) =>
                setModeMutation.mutate({ slug: pilot.slug, mode })
              }
              pending={
                setModeMutation.isPending &&
                setModeMutation.variables?.slug === pilot.slug
              }
            />
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
