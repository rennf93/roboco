"use client";

import { Task, TaskSessionLink, SessionScope } from "@/types";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { MessageSquare, ExternalLink, Star, Hash } from "lucide-react";
import Link from "next/link";

interface TabSessionsProps {
  task: Task;
}

const scopeColors: Record<SessionScope, string> = {
  [SessionScope.INITIATIVE]: "bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300",
  [SessionScope.CELL]: "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300",
  [SessionScope.TASK]: "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300",
};

const scopeLabels: Record<SessionScope, string> = {
  [SessionScope.INITIATIVE]: "Initiative",
  [SessionScope.CELL]: "Cell",
  [SessionScope.TASK]: "Task",
};

const relationshipLabels: Record<string, string> = {
  discussion: "Discussion",
  planning: "Planning",
  review: "Review",
  retrospective: "Retrospective",
};

function SessionCard({ session }: { session: TaskSessionLink }) {
  const shortSessionId = session.session_id.slice(0, 8);

  return (
    <Card className="hover:shadow-md transition-shadow">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <MessageSquare className="h-4 w-4 text-muted-foreground" />
            <CardTitle className="text-base font-medium">
              Session {shortSessionId}
            </CardTitle>
            {session.is_primary && (
              <Badge variant="default" className="gap-1">
                <Star className="h-3 w-3" />
                Primary
              </Badge>
            )}
          </div>
          <Badge className={scopeColors[session.scope]}>
            {scopeLabels[session.scope]}
          </Badge>
        </div>
        <CardDescription className="mt-1 flex items-center gap-2">
          <span>{relationshipLabels[session.relationship_type] || session.relationship_type}</span>
          <span className="text-muted-foreground">•</span>
          <span className="flex items-center gap-1">
            <Hash className="h-3 w-3" />
            {session.channel_slug}
          </span>
        </CardDescription>
      </CardHeader>
      <CardContent className="pt-0">
        <div className="flex justify-end">
          <Link href={`/communications/${session.session_id}`}>
            <Button variant="outline" size="sm" className="gap-2">
              <ExternalLink className="h-3 w-3" />
              View Session
            </Button>
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}

export function TabSessions({ task }: TabSessionsProps) {
  const sessions = task.sessions || [];

  if (sessions.length === 0) {
    return (
      <Card>
        <CardContent className="pt-6">
          <div className="text-center py-8">
            <MessageSquare className="h-12 w-12 mx-auto mb-4 text-muted-foreground/50" />
            <h3 className="text-lg font-medium mb-2">No Linked Sessions</h3>
            <p className="text-sm text-muted-foreground max-w-md mx-auto">
              This task does not have any linked discussion sessions yet.
              A PM will create a session when work begins.
            </p>
          </div>
        </CardContent>
      </Card>
    );
  }

  // Sort: primary first, then by scope
  const sortedSessions = [...sessions].sort((a, b) => {
    if (a.is_primary !== b.is_primary) return a.is_primary ? -1 : 1;
    return 0;
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-medium">Linked Sessions</h3>
          <p className="text-sm text-muted-foreground">
            Discussion sessions related to this task
          </p>
        </div>
        <Badge variant="outline">{sessions.length} session{sessions.length !== 1 ? 's' : ''}</Badge>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        {sortedSessions.map((session) => (
          <SessionCard key={session.session_id} session={session} />
        ))}
      </div>
    </div>
  );
}
