import Link from "next/link";
import { WaitingAgent } from "@/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { HelpTip } from "@/components/ui/help-tip";
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
        <HelpTip label="Agents blocked in a waiting_long state — refreshed every 10s" side="right">
          <CardTitle className="text-orange-500 flex items-center gap-2 w-fit">
            <AlertTriangle className="h-5 w-5" />
            Agents Waiting for Input
          </CardTitle>
        </HelpTip>
      </CardHeader>
      <CardContent>
        <div className="space-y-2">
          {waitingAgents.map((agent) => (
            <div
              key={agent.agent_id}
              className="flex items-center justify-between p-2 bg-muted rounded"
            >
              <div>
                <span className="font-medium">
                  {getAgentDisplayName(agent.agent_id)}
                </span>
                <HelpTip label="This agent is idle and will not progress until resolved">
                  <span className="text-muted-foreground ml-2">
                    waiting for: {agent.waiting_for}
                  </span>
                </HelpTip>
              </div>
              <HelpTip label="Opens this agent's detail page to send the resolution it needs">
                <Button variant="outline" size="sm" asChild>
                  <Link href={"/agents/" + agent.agent_id} prefetch={false}>
                    Resolve
                  </Link>
                </Button>
              </HelpTip>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
