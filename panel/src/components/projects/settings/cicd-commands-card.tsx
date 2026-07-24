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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { HelpTip } from "@/components/ui/help-tip";
import { Terminal } from "lucide-react";
import { toast } from "sonner";
import type { Project, ProjectUpdate } from "@/types";
import { SaveBar } from "./save-bar";

export function CicdCommandsCard({ project }: { project: Project }) {
  const updateProject = useUpdateProject();

  const [testCommand, setTestCommand] = useState(project.test_command || "");
  const [lintCommand, setLintCommand] = useState(project.lint_command || "");
  const [formatCommand, setFormatCommand] = useState(
    project.format_command || "",
  );
  const [typecheckCommand, setTypecheckCommand] = useState(
    project.typecheck_command || "",
  );
  const [buildCommand, setBuildCommand] = useState(project.build_command || "");
  const [qualityCommand, setQualityCommand] = useState(
    project.quality_command || "",
  );
  const [codegenCommand, setCodegenCommand] = useState(
    project.codegen_command || "",
  );

  const dirty =
    testCommand !== (project.test_command || "") ||
    lintCommand !== (project.lint_command || "") ||
    formatCommand !== (project.format_command || "") ||
    typecheckCommand !== (project.typecheck_command || "") ||
    buildCommand !== (project.build_command || "") ||
    qualityCommand !== (project.quality_command || "") ||
    codegenCommand !== (project.codegen_command || "");

  const handleSave = async () => {
    const updates: ProjectUpdate = {
      test_command: testCommand || undefined,
      lint_command: lintCommand || undefined,
      format_command: formatCommand || undefined,
      typecheck_command: typecheckCommand || undefined,
      build_command: buildCommand || undefined,
      quality_command: qualityCommand || undefined,
      codegen_command: codegenCommand || undefined,
    };

    try {
      await updateProject.mutateAsync({ projectId: project.id, updates });
      toast.success("CI/CD commands updated");
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
          <Terminal className="h-5 w-5" />
          CI/CD Commands
        </CardTitle>
        <CardDescription>
          Lint + Typecheck (or Quality Gate) run automatically at the dev&apos;s
          pre-submit gate; the rest are reference-only today
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-2">
          <HelpTip label="Reference only — not yet wired into any automated gate or CI run by RoboCo itself.">
            <Label htmlFor="test_command">Test Command</Label>
          </HelpTip>
          <Input
            id="test_command"
            value={testCommand}
            onChange={(e) => setTestCommand(e.target.value)}
            placeholder="uv run pytest"
          />
        </div>

        <div className="grid gap-2">
          <HelpTip label="Runs at the dev's pre-submit gate (i_am_done) alongside Typecheck — unless Quality Gate Command below is set, which replaces both.">
            <Label htmlFor="lint_command">Lint Command</Label>
          </HelpTip>
          <Input
            id="lint_command"
            value={lintCommand}
            onChange={(e) => setLintCommand(e.target.value)}
            placeholder="uv run ruff check ."
          />
        </div>

        <div className="grid gap-2">
          <HelpTip label="Reference only — deliberately excluded from the automated gate since formatting mutates files.">
            <Label htmlFor="format_command">Format Command</Label>
          </HelpTip>
          <Input
            id="format_command"
            value={formatCommand}
            onChange={(e) => setFormatCommand(e.target.value)}
            placeholder="uv run ruff format ."
          />
        </div>

        <div className="grid gap-2">
          <HelpTip label="Runs at the dev's pre-submit gate (i_am_done) alongside Lint — unless Quality Gate Command below is set, which replaces both.">
            <Label htmlFor="typecheck_command">Typecheck Command</Label>
          </HelpTip>
          <Input
            id="typecheck_command"
            value={typecheckCommand}
            onChange={(e) => setTypecheckCommand(e.target.value)}
            placeholder="uv run mypy src/"
          />
        </div>

        <div className="grid gap-2">
          <HelpTip label="Reference only — not run automatically; the slow build/test suite is left to CI.">
            <Label htmlFor="build_command">Build Command</Label>
          </HelpTip>
          <Input
            id="build_command"
            value={buildCommand}
            onChange={(e) => setBuildCommand(e.target.value)}
            placeholder="pnpm build"
          />
        </div>

        <div className="grid gap-2">
          <HelpTip label="When set, replaces the Lint + Typecheck pair as the dev's complete pre-submit gate command.">
            <Label htmlFor="quality_command">Quality Gate Command</Label>
          </HelpTip>
          <Input
            id="quality_command"
            value={qualityCommand}
            onChange={(e) => setQualityCommand(e.target.value)}
            placeholder="make gate"
          />
          <p className="text-xs text-muted-foreground">
            Fast pre-submit gate (lint + types + complexity, no tests) run in
            the dev&apos;s workspace at hand-off to QA.
          </p>
        </div>

        <div className="grid gap-2">
          <HelpTip label="Command that regenerates checked-in generated files, e.g. `make codegen`; run and committed before push so codegen drift never fails CI. Leave blank if the project has no generated artifacts.">
            <Label htmlFor="codegen_command">Codegen Command</Label>
          </HelpTip>
          <Input
            id="codegen_command"
            value={codegenCommand}
            onChange={(e) => setCodegenCommand(e.target.value)}
            placeholder="make codegen"
          />
          <p className="text-xs text-muted-foreground">
            Regenerates checked-in generated artifacts; any drift is committed
            in the task&apos;s workspace before each push.
          </p>
        </div>

        <SaveBar
          dirty={dirty}
          pending={updateProject.isPending}
          onSave={handleSave}
        />
      </CardContent>
    </Card>
  );
}
