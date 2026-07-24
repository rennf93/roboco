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
import { Waypoints } from "lucide-react";
import { toast } from "sonner";
import type { Project, ProjectUpdate } from "@/types";
import { EnvironmentLadderEditor } from "@/components/projects/environment-ladder-editor";
import { validateLadder } from "@/components/projects/ladder-validation";
import { SaveBar } from "./save-bar";

export function EnvironmentsCard({ project }: { project: Project }) {
  const updateProject = useUpdateProject();
  const [environments, setEnvironments] = useState(project.environments);

  const dirty =
    JSON.stringify(environments) !== JSON.stringify(project.environments);

  const handleSave = async () => {
    const envError = validateLadder(environments);
    if (envError) {
      toast.error(envError);
      return;
    }

    const updates: ProjectUpdate = { environments };

    try {
      await updateProject.mutateAsync({ projectId: project.id, updates });
      toast.success("Environment ladder updated");
    } catch (error) {
      toast.error(
        `Failed to update: ${error instanceof Error ? error.message : "Unknown error"}`,
      );
    }
  };

  return (
    <Card className="lg:col-span-2">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Waypoints className="h-5 w-5" />
          Environments
        </CardTitle>
        <CardDescription>
          Ordered promotion ladder from head (PR target) to prod (release
          target)
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <EnvironmentLadderEditor
          rungs={environments}
          onChange={setEnvironments}
        />

        <SaveBar
          dirty={dirty}
          pending={updateProject.isPending}
          onSave={handleSave}
        />
      </CardContent>
    </Card>
  );
}
