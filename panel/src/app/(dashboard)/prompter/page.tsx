"use client";

import { useCallback, useState } from "react";
import { History, MessageSquarePlus, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { ZeroState } from "@/components/prompter/zero-state";
import { ChatInterface } from "@/components/prompter/chat-interface";
import { DraftPanel } from "@/components/prompter/draft-panel";
import { LaunchSummary } from "@/components/prompter/launch-summary";
import { ConversationHistory } from "@/components/prompter/conversation-history";
import { ModelSelector } from "@/components/prompter/model-selector";
import { usePrompterStore } from "@/store/prompter-store";
import { tasksApi } from "@/lib/api/tasks";
import { toast } from "sonner";
import type { TaskCreate } from "@/types";

export default function PrompterPage() {
  const [showHistory, setShowHistory] = useState(false);
  const [pendingDraft, setPendingDraft] = useState<TaskCreate | null>(null);
  const [isLaunching, setIsLaunching] = useState(false);

  const activeConversationId = usePrompterStore((s) => s.activeConversationId);
  const showLaunchSummary = usePrompterStore((s) => s.showLaunchSummary);
  const startConversation = usePrompterStore((s) => s.startConversation);
  const setShowLaunchSummary = usePrompterStore((s) => s.setShowLaunchSummary);
  const markLaunched = usePrompterStore((s) => s.markLaunched);
  const addUserMessage = usePrompterStore((s) => s.addUserMessage);
  const openConversation = usePrompterStore((s) => s.openConversation);

  const handleSelectPrompt = useCallback(
    (prompt: string) => {
      const id = startConversation();
      openConversation(id);
      // Trigger send via the ChatInterface by storing the initial prompt
      // We'll add the user message and the ChatInterface will handle it
      addUserMessage(prompt);
      // Signal ChatInterface to auto-send via a state flag
      // (ChatInterface watches the last user message at mount)
    },
    [startConversation, openConversation, addUserMessage]
  );

  const handleNewConversation = useCallback(() => {
    startConversation();
  }, [startConversation]);

  const handleLaunch = useCallback((values: TaskCreate) => {
    setPendingDraft(values);
    setShowLaunchSummary(true);
  }, [setShowLaunchSummary]);

  const handleConfirmLaunch = useCallback(async () => {
    if (!pendingDraft || !activeConversationId) return;
    setIsLaunching(true);
    try {
      const task = await tasksApi.create(pendingDraft);
      markLaunched(activeConversationId);
      setShowLaunchSummary(false);
      toast.success(`Task "${task.title}" created and queued for your team!`);
    } catch (err) {
      console.error("[Prompter] Launch failed:", err);
      toast.error("Failed to create task. Please try again.");
    } finally {
      setIsLaunching(false);
    }
  }, [pendingDraft, activeConversationId, markLaunched, setShowLaunchSummary]);

  const handleBackFromSummary = useCallback(() => {
    setShowLaunchSummary(false);
  }, [setShowLaunchSummary]);

  const hasActiveConversation = !!activeConversationId;

  return (
    <div className="flex h-full gap-0 -m-6 overflow-hidden">
      {/* ── Left sidebar: history ── */}
      <aside
        className={`flex flex-col border-r bg-background transition-all duration-200 ${
          showHistory ? "w-64" : "w-12"
        } shrink-0`}
      >
        <div className="flex h-12 items-center justify-between px-2 border-b">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setShowHistory(!showHistory)}
            title={showHistory ? "Hide history" : "Show history"}
          >
            <History className="h-4 w-4" />
          </Button>
          {showHistory && (
            <Button
              variant="ghost"
              size="icon"
              onClick={handleNewConversation}
              title="New conversation"
            >
              <Plus className="h-4 w-4" />
            </Button>
          )}
        </div>
        {showHistory && (
          <ScrollArea className="flex-1">
            <ConversationHistory />
          </ScrollArea>
        )}
      </aside>

      {/* ── Main: zero-state or chat ── */}
      <main className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* Top bar */}
        <div className="flex h-12 items-center justify-between border-b px-4 shrink-0">
          <div className="flex items-center gap-2">
            <MessageSquarePlus className="h-4 w-4 text-primary" />
            <span className="font-semibold text-sm">Prompter</span>
          </div>
          <div className="flex items-center gap-2">
            {hasActiveConversation && (
              <>
                <ModelSelector />
                <Separator orientation="vertical" className="h-6" />
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={handleNewConversation}
                >
                  <Plus className="h-4 w-4 mr-1" />
                  New
                </Button>
              </>
            )}
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-hidden">
          {!hasActiveConversation ? (
            <ZeroState onSelectPrompt={handleSelectPrompt} />
          ) : (
            <ChatInterface conversationId={activeConversationId} />
          )}
        </div>
      </main>

      {/* ── Right panel: draft or launch summary ── */}
      {hasActiveConversation && (
        <aside className="w-80 shrink-0 border-l bg-background overflow-hidden flex flex-col">
          <div className="flex h-12 items-center px-4 border-b">
            <span className="font-semibold text-sm">
              {showLaunchSummary ? "Launch Preview" : "Draft"}
            </span>
          </div>
          <div className="flex-1 overflow-hidden">
            {showLaunchSummary && pendingDraft ? (
              <LaunchSummary
                draft={pendingDraft}
                onBack={handleBackFromSummary}
                onConfirm={handleConfirmLaunch}
                isLaunching={isLaunching}
              />
            ) : (
              <DraftPanel onLaunch={handleLaunch} />
            )}
          </div>
        </aside>
      )}
    </div>
  );
}
