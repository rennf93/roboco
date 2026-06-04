"use client";

import { formatDistanceToNow } from "date-fns";
import { MessageCircle, CheckCircle2, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { usePrompterStore, type Conversation } from "@/store/prompter-store";

function StatusIcon({ status }: { status: Conversation["status"] }) {
  if (status === "launched")
    return <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />;
  if (status === "abandoned")
    return <XCircle className="h-3.5 w-3.5 text-muted-foreground" />;
  return <MessageCircle className="h-3.5 w-3.5 text-primary" />;
}

export function ConversationHistory() {
  const conversations = usePrompterStore((s) => s.conversations);
  const activeId = usePrompterStore((s) => s.activeConversationId);
  const openConversation = usePrompterStore((s) => s.openConversation);

  const sorted = Object.values(conversations).sort(
    (a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime()
  );

  if (sorted.length === 0) {
    return (
      <div className="p-4 text-sm text-muted-foreground text-center">
        No past conversations
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-0.5 py-1">
      {sorted.map((conv) => (
        <button
          key={conv.id}
          onClick={() => openConversation(conv.id)}
          className={cn(
            "flex items-start gap-2 rounded-md px-3 py-2 text-left text-sm transition-colors",
            conv.id === activeId
              ? "bg-muted text-foreground"
              : "text-muted-foreground hover:bg-muted/50 hover:text-foreground"
          )}
        >
          <StatusIcon status={conv.status} />
          <div className="flex-1 min-w-0">
            <div className="font-medium truncate">{conv.title}</div>
            <div className="text-xs text-muted-foreground">
              {formatDistanceToNow(new Date(conv.updatedAt), {
                addSuffix: true,
              })}
              {" · "}
              <span className="capitalize">{conv.status}</span>
            </div>
          </div>
        </button>
      ))}
    </div>
  );
}
