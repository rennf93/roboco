import Link from "next/link";
import { WaitingAgent } from "@/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { AlertTriangle } from "lucide-react";
import { getAgentDisplayName } from "@/lib/agent-utils";

interface WaitingAgentsAlertProps {
  waitingAgents: WaitingAgent[];
}

export function WaitingAgentsAlert({ waitingAgents }: WaitingAgentsAlertProps) {
  if (waitingAgents.length === 0) return null;

  return (
    <Card className="border-orange-500/50">
      <CardHeader>
        <CardTitle className="text-orange-500 flex items-center gap-2">
          <AlertTriangle className="h-5 w-5" />
          Agents Waiting for Input
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="space-y-2">
          {waitingAgents.map((agent) => (
            <div key={agent.agent_id} className="flex items-center justify-between p-2 bg-muted rounded">
              <div>
                <span className="font-medium">{getAgentDisplayName(agent.agent_id)}</span>
                <span className="text-muted-foreground ml-2">waiting for: {agent.waiting_for}</span>
              </div>
              <Button variant="outline" size="sm" asChild>
                <Link href={"/agents/" + agent.agent_id}>Resolve</Link>
              </Button>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
