"use client";

import { TeamHealth } from "@/types";
import { TeamHealthCard } from "./team-health-card";
import { Skeleton } from "@/components/ui/skeleton";

interface TeamHealthCardsProps {
  teams: TeamHealth[] | undefined;
  isLoading: boolean;
}

export function TeamHealthCards({ teams, isLoading }: TeamHealthCardsProps) {
  if (isLoading) {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {[...Array(4)].map((_, i) => (
          <Skeleton key={i} className="h-48" />
        ))}
      </div>
    );
  }

  if (!teams || teams.length === 0) {
    return (
      <div className="text-center py-8 text-muted-foreground">
        No team health data available
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
      {teams.map((health) => (
        <TeamHealthCard key={health.team} health={health} />
      ))}
    </div>
  );
}
