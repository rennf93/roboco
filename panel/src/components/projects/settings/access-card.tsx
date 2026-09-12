"use client";

import { useState } from "react";
import {
  useGrantProjectAccess,
  useRevokeProjectAccess,
} from "@/hooks/use-projects";
import { useAgentDefinitions } from "@/hooks/use-agents";
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
import { AgentSelector } from "@/components/agents/agent-selector";
import { toast } from "sonner";
import { Lock, Plus, ShieldCheck, X } from "lucide-react";
import type { AllowedAgentSummary, Project } from "@/types";

// What "who can work this project" looks like per state:
// - unrestricted: every agent of the assigned cell (the historical default);
// - restricted: only the agents on the allowed list (empty list = nobody).
function RestrictedListRow({
  allowed,
  projectId,
  pending,
  onRemove,
}: {
  allowed: AllowedAgentSummary;
  projectId: string;
  pending: boolean;
  onRemove: (projectId: string, agentId: string, name: string) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-2 rounded border px-3 py-2">
      <div className="min-w-0">
        <p className="truncate text-sm font-medium">{allowed.name}</p>
        <code className="text-xs text-muted-foreground">{allowed.slug}</code>
      </div>
      <HelpTip label="Remove this agent from the allowed list. If the list becomes empty, no agent can claim this project's tasks.">
        <Button
          variant="ghost"
          size="sm"
          aria-label={`Remove ${allowed.name}`}
          disabled={pending}
          onClick={() => onRemove(projectId, allowed.id, allowed.name)}
        >
          <X className="h-4 w-4" />
        </Button>
      </HelpTip>
    </div>
  );
}

export function AccessCard({ project }: { project: Project }) {
  const grant = useGrantProjectAccess();
  const revoke = useRevokeProjectAccess();
  const { data: roster = [], isLoading: rosterLoading } = useAgentDefinitions();

  const restricted = project.access_restricted === true;
  const allowed = project.allowed_agents ?? [];
  const pending = grant.isPending || revoke.isPending;

  // The assigned cell's roster (the picker is cell-scoped — the access model
  // restricts a subset of the cell, never across cells), and the not-yet-
  // allowed subset for the "who can still be added" affordance. The allowed
  // list carries UUIDs while the roster keys pickers by slug, so match both.
  const cellRoster = roster.filter((a) => a.team === project.assigned_cell);
  const candidates = cellRoster.filter(
    (a) => !allowed.some((w) => w.id === a.uuid || w.slug === a.id),
  );
  const [pickedSlug, setPickedSlug] = useState<string | null>(null);

  const handleAdd = async () => {
    const candidate = cellRoster.find((c) => c.id === pickedSlug);
    if (!candidate) return;
    if (
      allowed.some((w) => w.id === candidate.uuid || w.slug === candidate.id)
    ) {
      toast.info(`${candidate.name} is already on the allowed list`);
      setPickedSlug(null);
      return;
    }
    try {
      await grant.mutateAsync({
        projectId: project.id,
        agentId: candidate.uuid,
      });
      toast.success(`${candidate.name} can now access this project`);
      setPickedSlug(null);
    } catch (error) {
      toast.error(
        `Failed to add access: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    }
  };

  const handleRemove = async (
    projectId: string,
    agentId: string,
    name: string,
  ) => {
    try {
      await revoke.mutateAsync({ projectId, agentId });
      toast.success(`${name} removed from the allowed list`);
    } catch (error) {
      toast.error(
        `Failed to remove access: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Lock className="h-5 w-5" />
          Access
        </CardTitle>
        <CardDescription>
          Which agents may work this project — the whole assigned cell by
          default, or an explicit allow-list
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {restricted ? (
          <div className="space-y-2">
            <HelpTip label="Only the listed agents may claim this project's tasks; every other agent of the cell is excluded.">
              <Badge
                variant="outline"
                className="border-amber-300 text-amber-700 dark:border-amber-800 dark:text-amber-300 w-fit"
              >
                <ShieldCheck className="mr-1 h-3 w-3" />
                Restricted to {allowed.length} agent
                {allowed.length === 1 ? "" : "s"}
              </Badge>
            </HelpTip>
            {allowed.length > 0 ? (
              <div className="space-y-2">
                {allowed.map((w) => (
                  <RestrictedListRow
                    key={w.id}
                    allowed={w}
                    projectId={project.id}
                    pending={pending}
                    onRemove={handleRemove}
                  />
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                The allowed list is empty — no agent can claim this
                project&apos;s tasks. Add one below, or grant access to restore
                the cell default.
              </p>
            )}
          </div>
        ) : (
          <HelpTip
            label="No restriction is in place: every agent assigned to the cell can claim this project's tasks."
          >
            <Badge
              variant="outline"
              className="border-green-300 text-green-700 dark:border-green-800 dark:text-green-300 w-fit"
            >
              All agents in the {project.assigned_cell} cell (default)
            </Badge>
          </HelpTip>
        )}

        <div className="space-y-2">
          <HelpTip label="Adding the first agent switches the project from the whole-cell default to a restricted allow-list.">
            <p className="text-sm font-medium w-fit">Restrict access</p>
          </HelpTip>
          {rosterLoading ? (
            <p className="text-xs text-muted-foreground">
              Loading the {project.assigned_cell} roster...
            </p>
          ) : candidates.length > 0 ? (
            <div className="flex items-center gap-2">
              <AgentSelector
                value={pickedSlug}
                onChange={setPickedSlug}
                filterByTeam={project.assigned_cell}
                allowClear={false}
                placeholder="Choose an agent..."
                disabled={pending}
              />
              <Button
                size="sm"
                disabled={!pickedSlug || pending}
                onClick={handleAdd}
              >
                <Plus className="h-4 w-4 mr-1" />
                Add
              </Button>
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">
              Every {project.assigned_cell} agent is already on the allowed
              list.
            </p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
