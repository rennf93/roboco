"use client";

import { Checkpoint } from "@/types";
import { Card, CardContent } from "@/components/ui/card";
import { Bookmark, Clock, User, ListTodo, FileText } from "lucide-react";
import { getAgentDisplayName } from "@/lib/agent-utils";

interface CheckpointCardProps {
  checkpoint: Checkpoint;
}

function formatTime(timestamp: string): string {
  const date = new Date(timestamp);
  return date.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function CheckpointCard({ checkpoint }: CheckpointCardProps) {
  return (
    <Card className="overflow-hidden">
      <div className="bg-primary/10 px-4 py-2 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Bookmark className="h-4 w-4 text-primary" />
          <span className="font-medium text-sm">Checkpoint</span>
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Clock className="h-3 w-3" />
          {formatTime(checkpoint.timestamp)}
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
          <h4 className="text-sm font-medium mb-1">State Summary</h4>
          <p className="text-sm text-muted-foreground whitespace-pre-wrap">
            {checkpoint.state_summary}
          </p>
        </div>

        {/* Remaining Work */}
        {checkpoint.remaining_work.length > 0 && (
          <div className="mb-4">
            <div className="flex items-center gap-2 mb-2">
              <ListTodo className="h-4 w-4 text-muted-foreground" />
              <h4 className="text-sm font-medium">Remaining Work</h4>
            </div>
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
            <div className="flex items-center gap-2 mb-2">
              <FileText className="h-4 w-4 text-muted-foreground" />
              <h4 className="text-sm font-medium">Notes</h4>
            </div>
            <p className="text-sm text-muted-foreground whitespace-pre-wrap">
              {checkpoint.notes}
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
