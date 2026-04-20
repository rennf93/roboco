"use client";

import { Message } from "@/types";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Markdown } from "@/components/ui/markdown";
import { MessageTypeBadge } from "./message-type-badge";
import { Clock, Link2 } from "lucide-react";
import Link from "next/link";
import { getAgentDisplayName, getAgentInitials } from "@/lib/agent-utils";

interface MessageItemProps {
  message: Message;
}

function formatTime(timestamp: string): string {
  const date = new Date(timestamp);
  return date.toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function MessageItem({ message }: MessageItemProps) {
  return (
    <div className="flex gap-3 py-3 hover:bg-muted/30 px-2 rounded-lg">
      <Avatar className="h-8 w-8 shrink-0">
        <AvatarFallback className="bg-primary/10 text-primary text-xs">
          {getAgentInitials(message.agent_id)}
        </AvatarFallback>
      </Avatar>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-medium text-sm">{getAgentDisplayName(message.agent_id)}</span>
          <span className="text-xs text-muted-foreground flex items-center gap-1">
            <Clock className="h-3 w-3" />
            {formatTime(message.timestamp)}
          </span>
          <MessageTypeBadge type={message.type} />
        </div>
        <div className="text-sm mt-1">
          <Markdown>{message.content}</Markdown>
        </div>
        {/* Mentions */}
        {message.mentions.length > 0 && (
          <div className="flex items-center gap-1 mt-2">
            {message.mentions.map((mention) => (
              <Badge key={mention} variant="outline" className="text-xs">
                @{mention.slice(0, 8)}
              </Badge>
            ))}
          </div>
        )}
        {/* Related Task */}
        {message.task_id && (
          <Link href={"/tasks/" + message.task_id}>
            <Badge variant="outline" className="text-xs mt-2 hover:bg-muted">
              <Link2 className="h-3 w-3 mr-1" />
              Task #{message.task_id.slice(0, 8)}
            </Badge>
          </Link>
        )}
      </div>
    </div>
  );
}
