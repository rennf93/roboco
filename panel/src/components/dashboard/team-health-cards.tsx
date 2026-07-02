"use client";

import { TeamHealth } from "@/types";
import { TeamHealthCard } from "./team-health-card";
import { Skeleton } from "@/components/ui/skeleton";
import { Sparkles, Bot } from "lucide-react";
import Link from "next/link";

interface TeamHealthCardsProps {
  teams: TeamHealth[] | undefined;
  isLoading: boolean;
}

/** Static link-card for on-demand agents (Intake, Secretary) that are not
 *  part of any standing team and therefore never appear in the API health data. */
function OnDemandAgentCard({
  title,
  href,
  icon: Icon,
  description,
}: {
  title: string;
  href: string;
  icon: React.ElementType;
  description: string;
}) {
  return (
    <Link href={href} className="block" prefetch={false}>
      <div className="rounded-lg border bg-card p-4 hover:bg-accent/50 transition-colors h-full flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <Icon className="h-4 w-4 text-muted-foreground" />
          <span className="font-medium text-sm">{title}</span>
          <span className="ml-auto text-xs rounded-full bg-secondary px-2 py-0.5 text-secondary-foreground">
            On-Demand
          </span>
        </div>
        <p className="text-xs text-muted-foreground">{description}</p>
      </div>
    </Link>
  );
}

export function TeamHealthCards({ teams, isLoading }: TeamHealthCardsProps) {
  if (isLoading) {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 xl:grid-cols-4 2xl:grid-cols-6 gap-4">
        {[...Array(4)].map((_, i) => (
          <Skeleton key={i} className="h-48" />
        ))}
      </div>
    );
  }

  const hasTeams = teams && teams.length > 0;

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 xl:grid-cols-4 2xl:grid-cols-6 gap-4">
      {hasTeams ? (
        teams.map((health) => (
          <TeamHealthCard key={health.team} health={health} />
        ))
      ) : (
        <div className="col-span-full text-center py-8 text-muted-foreground">
          No team health data available
        </div>
      )}

      {/* Static on-demand agent cards — always visible regardless of API data */}
      <OnDemandAgentCard
        title="Task Intake"
        href="/prompter"
        icon={Sparkles}
        description="Intake interviewer — chat with CEO to draft and submit new tasks"
      />
      <OnDemandAgentCard
        title="Secretary"
        href="/business?tab=secretary"
        icon={Bot}
        description="Secretary agent — manage business goals, pitches, and notes"
      />
    </div>
  );
}
