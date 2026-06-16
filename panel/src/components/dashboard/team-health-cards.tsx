"use client";

import Link from "next/link";
import { TeamHealth } from "@/types";
import { TeamHealthCard } from "./team-health-card";
import { Skeleton } from "@/components/ui/skeleton";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Sparkles, Bot, ArrowRight } from "lucide-react";

interface TeamHealthCardsProps {
  teams: TeamHealth[] | undefined;
  isLoading: boolean;
}

/** Static card for on-demand agents (Intake, Secretary) that don't belong to a permanent team. */
function OnDemandAgentCard({
  title,
  description,
  icon,
  href,
}: {
  title: string;
  description: string;
  icon: React.ReactNode;
  href: string;
}) {
  return (
    <Link href={href} className="block group">
      <Card className="hover:shadow-md transition-shadow cursor-pointer group-hover:border-primary/50">
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="text-lg flex items-center gap-2">
              {icon}
              {title}
            </CardTitle>
            <Badge variant="secondary" className="text-xs">On-Demand</Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm text-muted-foreground">{description}</p>
          <div className="flex items-center gap-1 text-xs text-primary font-medium">
            Open
            <ArrowRight className="h-3 w-3" />
          </div>
        </CardContent>
      </Card>
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

  if (!teams || teams.length === 0) {
    return (
      <div className="text-center py-8 text-muted-foreground">
        No team health data available
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 xl:grid-cols-4 2xl:grid-cols-6 gap-4">
      {teams.map((health) => (
        <TeamHealthCard key={health.team} health={health} />
      ))}

      {/* On-demand agents: always shown alongside team health */}
      <OnDemandAgentCard
        title="Task Intake"
        description="On-demand AI interviewer that chats with you to draft and scope new tasks."
        icon={<Sparkles className="h-5 w-5 text-primary shrink-0" />}
        href="/prompter"
      />
      <OnDemandAgentCard
        title="Secretary"
        description="Chief-of-staff AI that proposes strategic directives and summarises board decisions."
        icon={<Bot className="h-5 w-5 text-indigo-500 shrink-0" />}
        href="/business?tab=secretary"
      />
    </div>
  );
}
