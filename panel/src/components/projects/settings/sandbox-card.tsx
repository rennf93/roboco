"use client";

import { useState } from "react";
import { useUpdateProject } from "@/hooks/use-projects";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { HelpTip } from "@/components/ui/help-tip";
import { Database } from "lucide-react";
import { toast } from "sonner";
import type { Project, ProjectUpdate } from "@/types";
import { SaveBar } from "./save-bar";

const SANDBOX_SERVICES = [
  { id: "postgres", label: "PostgreSQL" },
  { id: "redis", label: "Redis" },
  { id: "mongo", label: "MongoDB" },
] as const;

// Activatable extensions/modules per service, mirroring the backend allowlist
// (roboco/models/sandbox.py SANDBOX_ENGINE_FEATURES). The allowlist is the
// security containment — a plpython3u (superuser-RCE) is absent by design.
// Mongo has no activatable features and is intentionally absent here.
const SANDBOX_EXTENSIONS: Record<
  string,
  { id: string; label: string; hint: string }[]
> = {
  postgres: [
    {
      id: "vector",
      label: "pgvector",
      hint: "Vector similarity search/indexing for embeddings.",
    },
    {
      id: "postgis",
      label: "PostGIS",
      hint: "Geospatial types and queries for PostgreSQL.",
    },
    {
      id: "pg_trgm",
      label: "pg_trgm",
      hint: "Trigram-based fuzzy text matching and similarity search.",
    },
    {
      id: "citext",
      label: "citext",
      hint: "Case-insensitive text column type.",
    },
    {
      id: "uuid-ossp",
      label: "uuid-ossp",
      hint: "Functions to generate UUIDs (e.g. uuid_generate_v4()).",
    },
  ],
  redis: [
    {
      id: "search",
      label: "RediSearch",
      hint: "Full-text search and secondary indexing for Redis.",
    },
    {
      id: "json",
      label: "RedisJSON",
      hint: "Native JSON document storage and querying.",
    },
    {
      id: "bloom",
      label: "RedisBloom",
      hint: "Probabilistic data structures (Bloom/Cuckoo filters, HyperLogLog).",
    },
  ],
};

const SANDBOX_SERVICE_HINTS: Record<string, string> = {
  postgres:
    "Ephemeral PostgreSQL container for this project's agent spawns — random creds, tmpfs storage, torn down at end of engagement.",
  redis:
    "Ephemeral Redis container for this project's agent spawns — random creds, tmpfs storage, torn down at end of engagement.",
  mongo:
    "Ephemeral MongoDB container for this project's agent spawns — random creds, tmpfs storage, torn down at end of engagement.",
};

function extensionsEqual(
  a: Record<string, Set<string>>,
  b: Record<string, string[]>,
): boolean {
  const aKeys = Object.keys(a).filter((k) => a[k].size > 0);
  const bKeys = Object.keys(b).filter((k) => (b[k] || []).length > 0);
  if (aKeys.length !== bKeys.length) return false;
  return aKeys.every((k) => {
    const bSet = new Set(b[k] || []);
    return a[k].size === bSet.size && [...a[k]].every((f) => bSet.has(f));
  });
}

export function SandboxCard({ project }: { project: Project }) {
  const updateProject = useUpdateProject();

  const initialServices = project.sandbox_services || [];
  const [sandboxSet, setSandboxSet] = useState<Set<string>>(
    new Set(initialServices),
  );
  const [sandboxExtensions, setSandboxExtensions] = useState<
    Record<string, Set<string>>
  >(() => {
    const init: Record<string, Set<string>> = {};
    for (const [svc, feats] of Object.entries(
      project.sandbox_extensions || {},
    )) {
      init[svc] = new Set(feats);
    }
    return init;
  });

  const toggleExtension = (svc: string, feat: string, checked: boolean) => {
    setSandboxExtensions((prev) => {
      const next = { ...prev };
      const set = new Set(next[svc] ?? []);
      if (checked) set.add(feat);
      else set.delete(feat);
      next[svc] = set;
      return next;
    });
  };

  const savedServices = new Set(project.sandbox_services || []);
  const servicesEqual =
    sandboxSet.size === savedServices.size &&
    [...sandboxSet].every((s) => savedServices.has(s));
  const dirty =
    !servicesEqual ||
    !extensionsEqual(sandboxExtensions, project.sandbox_extensions || {});

  const handleSave = async () => {
    const updates: ProjectUpdate = {
      sandbox_services: [...sandboxSet],
      sandbox_extensions: (() => {
        const extObj: Record<string, string[]> = {};
        for (const svc of sandboxSet) {
          const feats = sandboxExtensions[svc];
          if (feats && feats.size > 0) extObj[svc] = [...feats].sort();
        }
        return extObj;
      })(),
    };

    try {
      await updateProject.mutateAsync({ projectId: project.id, updates });
      toast.success("Sandbox settings updated");
    } catch (error) {
      toast.error(
        `Failed to update: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Database className="h-5 w-5" />
          Sandbox
        </CardTitle>
        <CardDescription>
          Ephemeral dev DB/Redis/Mongo per agent spawn, requested on-demand
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-2">
          <HelpTip label="Requires the sandbox engine armed fleet-wide (ROBOCO_SANDBOX_DB_ENABLED); agents call request_sandbox() on-demand rather than getting creds at spawn.">
            <Label>Sandbox Services</Label>
          </HelpTip>
          {SANDBOX_SERVICES.map((svc) => (
            <div key={svc.id} className="flex items-center justify-between">
              <HelpTip label={SANDBOX_SERVICE_HINTS[svc.id]}>
                <Label
                  htmlFor={`sandbox_${svc.id}`}
                  className="text-sm font-normal"
                >
                  {svc.label}
                </Label>
              </HelpTip>
              <Switch
                id={`sandbox_${svc.id}`}
                checked={sandboxSet.has(svc.id)}
                onCheckedChange={(checked) =>
                  setSandboxSet((prev) => {
                    const next = new Set(prev);
                    if (checked) next.add(svc.id);
                    else next.delete(svc.id);
                    return next;
                  })
                }
              />
            </div>
          ))}
          <p className="text-xs text-muted-foreground">
            Provision a throwaway sandbox DB/Redis per agent spawn for this
            project instead of the production credentials.
          </p>
        </div>

        {SANDBOX_SERVICES.filter(
          (svc) => sandboxSet.has(svc.id) && SANDBOX_EXTENSIONS[svc.id],
        ).map((svc) => (
          <div key={`ext_${svc.id}`} className="grid gap-2">
            <Label>{svc.label} Extensions</Label>
            {SANDBOX_EXTENSIONS[svc.id].map((ext) => (
              <div key={ext.id} className="flex items-center justify-between">
                <HelpTip label={ext.hint}>
                  <Label
                    htmlFor={`ext_${svc.id}_${ext.id}`}
                    className="text-sm font-normal"
                  >
                    {ext.label}
                  </Label>
                </HelpTip>
                <Switch
                  id={`ext_${svc.id}_${ext.id}`}
                  checked={sandboxExtensions[svc.id]?.has(ext.id) ?? false}
                  onCheckedChange={(checked) =>
                    toggleExtension(svc.id, ext.id, checked)
                  }
                />
              </div>
            ))}
            <p className="text-xs text-muted-foreground">
              Activated on-demand in the sandbox {svc.label} container. Set the
              full set here so agents can request subsets.
            </p>
          </div>
        ))}

        <SaveBar
          dirty={dirty}
          pending={updateProject.isPending}
          onSave={handleSave}
        />
      </CardContent>
    </Card>
  );
}
