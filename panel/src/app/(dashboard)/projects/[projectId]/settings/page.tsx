"use client";

import { Suspense, use, useEffect } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useProject } from "@/hooks/use-projects";
import { usePageRefresh } from "@/hooks";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { AlertTriangle, ArrowLeft } from "lucide-react";
import { ConventionsTab } from "@/components/conventions/conventions-tab";
import { IdentityCard } from "@/components/projects/settings/identity-card";
import { GitAuthCard } from "@/components/projects/settings/git-auth-card";
import { PlacementCard } from "@/components/projects/settings/placement-card";
import { EnvironmentsCard } from "@/components/projects/settings/environments-card";
import { CicdCommandsCard } from "@/components/projects/settings/cicd-commands-card";
import { BudgetOpsCard } from "@/components/projects/settings/budget-ops-card";
import { SandboxCard } from "@/components/projects/settings/sandbox-card";

interface TabDef {
  value: "settings" | "conventions";
  label: string;
  hint: string;
}

const TAB_DEFS: TabDef[] = [
  {
    value: "settings",
    label: "Settings",
    hint: "Identity, git auth, placement, environments, CI/CD commands, budget, and sandbox — one card per concern",
  },
  {
    value: "conventions",
    label: "Conventions",
    hint: "The architectural placement map, rule set, and waivers for this repo",
  },
];
const TAB_VALUES = TAB_DEFS.map((t) => t.value);
type TabValue = (typeof TAB_VALUES)[number];

function isValidTab(value: string | null): value is TabValue {
  return TAB_VALUES.includes(value as TabValue);
}

function PageSkeleton() {
  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4">
        <Skeleton className="h-9 w-20" />
        <div className="space-y-2">
          <Skeleton className="h-8 w-64" />
          <Skeleton className="h-4 w-32" />
        </div>
      </div>
      <Skeleton className="h-9 w-56" />
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-64 w-full" />
        ))}
      </div>
    </div>
  );
}

function ProjectSettingsPageContent({ projectId }: { projectId: string }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { data: project, isLoading, error, refetch } = useProject(projectId);

  const { register, unregister } = usePageRefresh();
  useEffect(() => {
    const cb = () => {
      void refetch();
    };
    register(cb);
    return () => unregister(cb);
  }, [register, unregister, refetch]);

  const rawTab = searchParams.get("tab");
  const activeTab: TabValue = isValidTab(rawTab) ? rawTab : "settings";

  const handleTabChange = (value: string) => {
    const params = new URLSearchParams(searchParams.toString());
    params.set("tab", value);
    router.replace(`/projects/${projectId}/settings?${params.toString()}`);
  };

  if (isLoading) {
    return <PageSkeleton />;
  }

  if (error || !project) {
    return (
      <div className="space-y-6">
        <Link href="/projects" prefetch={false}>
          <Button variant="ghost" size="sm">
            <ArrowLeft className="h-4 w-4 mr-2" />
            Back to Projects
          </Button>
        </Link>
        <Card>
          <CardContent className="pt-6">
            <div className="text-center py-12">
              <AlertTriangle className="h-16 w-16 mx-auto mb-4 text-destructive" />
              <h2 className="text-xl font-semibold mb-2">Project Not Found</h2>
              <p className="text-muted-foreground mb-6">
                {error instanceof Error
                  ? error.message
                  : "The project you're looking for doesn't exist or has been deleted."}
              </p>
              <Link href="/projects" prefetch={false}>
                <Button>View All Projects</Button>
              </Link>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <Link href="/projects" prefetch={false}>
          <Button variant="ghost" size="sm">
            <ArrowLeft className="h-4 w-4 mr-2" />
            Back
          </Button>
        </Link>
        <div>
          <h1 className="text-3xl font-bold tracking-tight">{project.name}</h1>
          <p className="text-muted-foreground font-mono text-sm">
            {project.slug}
          </p>
        </div>
      </div>

      <Tabs value={activeTab} onValueChange={handleTabChange}>
        <TabsList>
          {TAB_DEFS.map((tab) => (
            <Tooltip key={tab.value}>
              <TooltipTrigger asChild>
                {/* TooltipTrigger's asChild Slot merge clobbers TabsTrigger's
                    own data-state; re-assert the real selection state
                    explicitly (see business/page.tsx) so the
                    data-[state=active] styling still fires. */}
                <TabsTrigger
                  value={tab.value}
                  data-state={tab.value === activeTab ? "active" : "inactive"}
                >
                  {tab.label}
                </TabsTrigger>
              </TooltipTrigger>
              <TooltipContent>{tab.hint}</TooltipContent>
            </Tooltip>
          ))}
        </TabsList>

        <TabsContent value="settings" className="mt-4">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <IdentityCard project={project} />
            <GitAuthCard project={project} />
            <PlacementCard project={project} />
            <EnvironmentsCard project={project} />
            <CicdCommandsCard project={project} />
            <BudgetOpsCard project={project} />
            <SandboxCard project={project} />
          </div>
        </TabsContent>

        <TabsContent value="conventions" className="mt-4">
          <ConventionsTab projectId={projectId} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

interface ProjectSettingsPageProps {
  params: Promise<{ projectId: string }>;
}

export default function ProjectSettingsPage({
  params,
}: ProjectSettingsPageProps) {
  const { projectId } = use(params);
  return (
    <Suspense fallback={<PageSkeleton />}>
      <ProjectSettingsPageContent projectId={projectId} />
    </Suspense>
  );
}
