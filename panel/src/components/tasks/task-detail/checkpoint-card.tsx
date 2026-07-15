"use client";

import { Checkpoint } from "@/types";
import { Card, CardContent } from "@/components/ui/card";
import { Bookmark, Clock, User, ListTodo, FileText } from "lucide-react";
import { getAgentDisplayName } from "@/lib/agent-utils";
import { formatAbsoluteTimestamp } from "@/lib/utils";
import { HelpTip } from "@/components/ui/help-tip";

interface CheckpointCardProps {
  checkpoint: Checkpoint;
}

export function CheckpointCard({ checkpoint }: CheckpointCardProps) {
  return (
    <Card className="overflow-hidden">
      <div className="bg-primary/10 px-4 py-2 flex items-center justify-between">
        <HelpTip label="A saved state snapshot an agent leaves so work can resume later without re-deriving context">
          <div className="flex items-center gap-2">
            <Bookmark className="h-4 w-4 text-primary" />
            <span className="font-medium text-sm">Checkpoint</span>
          </div>
        </HelpTip>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Clock className="h-3 w-3" />
          {formatAbsoluteTimestamp(checkpoint.timestamp)}
        </div>
      </div>
      <CardContent className="pt-4">
        {/* Agent */}
        <div className="flex items-center gap-2 text-sm text-muted-foreground mb-3">
          <User className="h-4 w-4" />
          <span>Saved by {getAgentDisplayName(checkpoint.agent_id)}</span>
        </div>

        {/* State Summary */}
        <div className="mb-4">
          <HelpTip label="What the agent understood to be true about the task when this checkpoint was saved">
            <h4 className="text-sm font-medium mb-1 w-fit">State Summary</h4>
          </HelpTip>
          <p className="text-sm text-muted-foreground whitespace-pre-wrap">
            {checkpoint.state_summary}
          </p>
        </div>

        {/* Remaining Work */}
        {checkpoint.remaining_work.length > 0 && (
          <div className="mb-4">
            <HelpTip label="Sub-steps the agent identified as still outstanding at checkpoint time">
              <div className="flex items-center gap-2 mb-2 w-fit">
                <ListTodo className="h-4 w-4 text-muted-foreground" />
                <h4 className="text-sm font-medium">Remaining Work</h4>
              </div>
            </HelpTip>
            <ul className="list-disc list-inside text-sm text-muted-foreground space-y-1">
              {checkpoint.remaining_work.map((item, idx) => (
                <li key={idx}>{item}</li>
              ))}
            </ul>
          </div>
        )}

        {/* Notes */}
        {checkpoint.notes && (
          <div>
            <HelpTip label="Free-form notes the checkpointing agent left for whoever resumes this task">
              <div className="flex items-center gap-2 mb-2 w-fit">
                <FileText className="h-4 w-4 text-muted-foreground" />
                <h4 className="text-sm font-medium">Notes</h4>
              </div>
            </HelpTip>
            <p className="text-sm text-muted-foreground whitespace-pre-wrap">
              {checkpoint.notes}
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
