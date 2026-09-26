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

export const PILOT_MODES = ["off", "shadow", "on"] as const;
export type PilotMode = (typeof PILOT_MODES)[number];

export type PilotDef = {
  slug: string;
  label: string;
  description: string;
};

export type PilotSection = {
  heading: string;
  pilots: PilotDef[];
};

export function pilotSettingKey(slug: string): string {
  return `decisions.pilot.${slug}`;
}

// Dotted tri-state settings keys (off | shadow | on), validated server-side by
// _validate_pilot_mode in roboco/services/settings.py. Keep the slug list in
// sync with that registry and with roboco/services/decisions/pilots.py. The
// "Cognition lane" section mirrors the Lane column of the Tier B table in
// docs/internal/jev-decisions-spec.md (section 7.1); adding a future pilot is
// one entry here.
const TIER_A_PILOTS: PilotDef[] = [
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
  {
    slug: "tool_spotlight",
    label: "Tool spotlight (spawn briefing)",
    description:
      "Highlights the 5-7 verbs that matter most for the current task in the spawn briefing; never removes verbs.",
  },
];

const COGNITION_LANE_PILOTS: PilotDef[] = [
  {
    slug: "pre_send_completeness",
    label: "Pre-send completeness (outbound)",
    description:
      "Hints when an outbound message does not answer what was asked.",
  },
  {
    slug: "context_pruning",
    label: "Context pruning (spawn detail)",
    description:
      "Drops stale task-detail entries at spawn (drop-only).",
  },
  {
    slug: "budget_wrapup",
    label: "Budget wrap-up (cap proximity)",
    description: "Graceful wrap-up choice near the budget cap.",
  },
  {
    slug: "delta_brief",
    label: "Delta brief (respawn catch-up)",
    description:
      'A "what changed while you were away" brief for respawned/resumed agents.',
  },
  {
    slug: "idle_legitimacy",
    label: "Idle legitimacy (activity check)",
    description:
      "Flags idle agents that are actually done or stranded.",
  },
  {
    slug: "review_queue_priority",
    label: "Review queue priority (QA depth)",
    description: "Orders the QA queue and sets review depth.",
  },
  {
    slug: "branch_staleness",
    label: "Branch staleness (base moved)",
    description:
      "Early warning when a base branch moved (add-only notifications).",
  },
  {
    slug: "lesson_prune",
    label: "Lesson prune (memory)",
    description:
      "Prunes inapplicable institutional-memory lessons (prune-only).",
  },
  {
    slug: "silent_exit",
    label: "Silent exit (handoff note)",
    description:
      "Handoff note instead of blind auto-substitute on silent exit.",
  },
];

const TIER_B_PILOTS: PilotDef[] = [
  {
    slug: "plan_quality",
    label: "Plan quality (decomposition gate)",
    description: "PM plan adequacy score for the decomposition gate.",
  },
  {
    slug: "injection_screen",
    label: "Injection screen (inbound text)",
    description:
      "Prompt-injection risk screen for inbound text (fail-closed).",
  },
  {
    slug: "intake_preroute",
    label: "Intake preroute (CEO free text)",
    description:
      "Routes CEO free text: intake chat, secretary directive, quick answer, or noise.",
  },
  {
    slug: "x_mention_triage",
    label: "X mention triage (draft call gate)",
    description:
      "Classifies X mentions before spending a draft call.",
  },
  {
    slug: "collision_edge",
    label: "Collision edge (sequencing)",
    description:
      "Adds sequencing edges for logical conflicts beyond file overlap (add-only).",
  },
  {
    slug: "ci_watch_route",
    label: "CI watch route (red-CI tasks)",
    description: "Routes red-CI tasks and sets urgency.",
  },
  {
    slug: "heal_severity",
    label: "Heal severity (self-heal)",
    description:
      "Scores self-heal fix severity instead of hardcoded MEDIUM.",
  },
  {
    slug: "release_worthy",
    label: "Release worthy (urgency score)",
    description:
      "Scores release urgency against the release threshold.",
  },
  {
    slug: "second_review_eligibility",
    label: "Second review eligibility (high stakes)",
    description: "Adds high-stakes second reviews (add-only).",
  },
  {
    slug: "segment_classify",
    label: "Segment classify (sessions)",
    description:
      "Classifies session segments, replacing the regex+LLM fallback.",
  },
  {
    slug: "vault_prefilter",
    label: "Vault prefilter (extraction)",
    description: "Skips extraction for notes that warrant no task.",
  },
  {
    slug: "notify_dedup",
    label: "Notify dedup (unacked notifications)",
    description: "Drops reworded duplicates of unacked notifications.",
  },
  {
    slug: "board_due_early",
    label: "Board due-early (board programs)",
    description: "Due-early and rotation choices for board programs.",
  },
  {
    slug: "external_pr_triage",
    label: "External PR triage (inbound PRs)",
    description:
      "Review priority plus a fail-closed injection screen for external PR text.",
  },
  {
    slug: "stranded_response",
    label: "Stranded response (escalate/respawn/wait)",
    description: "Escalate/respawn/wait for stranded tasks.",
  },
  {
    slug: "coroner_gate",
    label: "Coroner gate (postmortems)",
    description: "Scores whether a postmortem is warranted.",
  },
  {
    slug: "dep_update_risk",
    label: "Dep update risk (dependency updates)",
    description: "Scores dependency-update risk.",
  },
  {
    slug: "secretary_nl",
    label: "Secretary NL (directives)",
    description:
      "Resolves directive kind and assignee from natural language.",
  },
  {
    slug: "release_readiness",
    label: "Release readiness (CEO advisory)",
    description: "Advisory release-risk score for the CEO.",
  },
  {
    slug: "commit_intent",
    label: "Commit intent (diff mismatch)",
    description:
      "Hints when a commit message mismatches the diff intent.",
  },
  {
    slug: "tg_freetext_gate",
    label: "TG free-text gate (Telegram)",
    description:
      "Surfaces free text that deserves an answer (add-only).",
  },
  {
    slug: "memory_distill_gate",
    label: "Memory distill gate (lessons)",
    description: "Skips persisting low-value lessons.",
  },
  {
    slug: "changelog_highlights",
    label: "Changelog highlights (amplification)",
    description: "Picks the changelog highlight worth amplifying.",
  },
  {
    slug: "proactive_domain",
    label: "Proactive domain (inference)",
    description: "Infers the domain for proactive work.",
  },
  {
    slug: "idle_reaping",
    label: "Idle reaping (session TTL)",
    description: "Extends TTL for sessions that look active.",
  },
  {
    slug: "decision_note_sufficiency",
    label: "Decision note sufficiency (closure)",
    description: "Accepts short but substantive decision notes.",
  },
  {
    slug: "findings_mapping",
    label: "Findings mapping (QA hunks)",
    description:
      "Suggests which diff hunks address which QA findings (advisory).",
  },
  {
    slug: "board_evidence_skip",
    label: "Board evidence skip (no-op cycles)",
    description: "Skips no-op board-program cycles entirely.",
  },
  {
    slug: "respawn_verdict",
    label: "Respawn verdict (wedged tasks)",
    description: "Spawn/amend/hold/kill choice for wedged-task respawns.",
  },
  {
    slug: "submit_now_confidence",
    label: "Submit-now confidence (zero work)",
    description:
      "Confidence the remaining work is zero before the submit-now prompt branch.",
  },
  {
    slug: "park_cause",
    label: "Park cause (stranding)",
    description: "Classifies why an agent parked or stranded.",
  },
  {
    slug: "pm_closure_confidence",
    label: "PM closure confidence (advisory)",
    description:
      "Advisory closure-safety score in the PM closure prompt.",
  },
  {
    slug: "assembled_coherence",
    label: "Assembled coherence (PR review)",
    description: "AC-coverage scaffold for assembled-branch PR review.",
  },
];

export const PILOT_SECTIONS: PilotSection[] = [
  { heading: "Tier A pilots", pilots: TIER_A_PILOTS },
  { heading: "Cognition lane", pilots: COGNITION_LANE_PILOTS },
  { heading: "Tier B (second wave)", pilots: TIER_B_PILOTS },
];

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
  // that pilot (the backend treats a missing key the same way). Every pilot
  // defaults to off; shadow only logs, on acts.
  const storedModes = settings ?? {};

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Scale className="h-5 w-5" />
          Decisions Pilots
        </CardTitle>
        <CardDescription>
          Per-pilot mode for the Decisions service. All pilots default to off.
          &quot;shadow&quot; logs verdicts without acting on them;
          &quot;on&quot; acts on them. Changes persist immediately and take
          effect on the next backend restart. Needs the Decisions master flag
          on (Feature Flags below).
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading && (
          <p className="text-sm text-muted-foreground">Loading pilots…</p>
        )}
        {PILOT_SECTIONS.map((section) => (
          <section
            key={section.heading}
            aria-label={section.heading}
            className="mb-6 last:mb-0"
          >
            <h3
              data-testid={`pilot-section-${section.heading.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`}
              className="mb-2 text-sm font-semibold text-muted-foreground"
            >
              {section.heading}
            </h3>
            <div className="space-y-3">
              {section.pilots.map((pilot) => (
                <PilotRow
                  key={pilot.slug}
                  slug={pilot.slug}
                  label={pilot.label}
                  description={pilot.description}
                  value={
                    (storedModes[
                      pilotSettingKey(pilot.slug)
                    ] as PilotMode) ?? "off"
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
          </section>
        ))}
      </CardContent>
    </Card>
  );
}
