"use client";

import Link from "next/link";
import { CheckCircle2, ExternalLink, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { Team } from "@/types";

interface SuccessCardProps {
  taskId: string;
  taskTitle: string;
  team: Team;
  onStartAnother: () => void;
}

export function SuccessCard({
  taskId,
  taskTitle,
  team,
  onStartAnother,
}: SuccessCardProps) {
  return (
    <Card className="border-green-500/30 bg-green-500/5">
      <CardHeader className="pb-2">
        <div className="flex items-center gap-2">
          <CheckCircle2 className="h-5 w-5 text-green-600" />
          <CardTitle className="text-sm font-semibold text-green-700 dark:text-green-400">
            Task Created Successfully
          </CardTitle>
        </div>
      </CardHeader>

      <CardContent className="pb-3 space-y-2">
        <p className="text-sm font-medium">{taskTitle}</p>
        <div className="flex items-center gap-2">
          <Badge variant="secondary" className="text-xs">
            {team.replace("_", " ")}
          </Badge>
          <span className="text-xs text-muted-foreground">ID: {taskId.slice(0, 8)}…</span>
        </div>
      </CardContent>

      <CardFooter className="gap-2 pt-0">
        <Button variant="outline" size="sm" asChild className="flex-1">
          <Link href={`/tasks/${taskId}`} target="_blank" rel="noopener noreferrer">
            <ExternalLink className="mr-1.5 h-3.5 w-3.5" />
            View Task
          </Link>
        </Button>
        <Button size="sm" className="flex-1" onClick={onStartAnother}>
          <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
          Start Another
        </Button>
      </CardFooter>
    </Card>
  );
}
