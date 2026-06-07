"use client";

import { Sparkles } from "lucide-react";
import { usePrompter } from "@/hooks/use-prompter";
import {
  ChatMessages,
  ChatComposer,
  ConfirmDialog,
  SuccessCard,
} from "@/components/prompter";

export default function PrompterPage() {
  const {
    state,
    messages,
    isSending,
    editableDraft,
    createdTaskId,
    createdTaskTitle,
    createdTaskTeam,
    send,
    openReview,
    closeReview,
    keepChatting,
    updateDraft,
    isValidForLaunch,
    launchTask,
    startAnother,
    isLaunching,
  } = usePrompter();

  const isComposerDisabled =
    state === "launching" || state === "success";

  return (
    <div className="flex h-full flex-col">
      {/* Page header */}
      <div className="flex items-center gap-3 border-b px-6 py-4">
        <Sparkles className="h-5 w-5 text-primary" />
        <div>
          <h1 className="text-lg font-semibold">Task Assistant</h1>
          <p className="text-xs text-muted-foreground">
            Describe your idea and I&apos;ll help you create a structured task
          </p>
        </div>
      </div>

      {/* Chat area */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Success overlay in chat area */}
        {state === "success" &&
          createdTaskId &&
          createdTaskTitle &&
          createdTaskTeam ? (
          <div className="flex flex-1 flex-col items-center justify-center px-8 py-8">
            <div className="w-full max-w-md">
              <SuccessCard
                taskId={createdTaskId}
                taskTitle={createdTaskTitle}
                team={createdTaskTeam}
                onStartAnother={startAnother}
              />
            </div>
          </div>
        ) : (
          <ChatMessages
            messages={messages}
            onOpenReview={openReview}
            onKeepChatting={keepChatting}
          />
        )}

        {/* Composer */}
        <ChatComposer
          onSend={send}
          disabled={isComposerDisabled}
          isSending={isSending}
        />
      </div>

      {/* Confirmation dialog (portal) */}
      <ConfirmDialog
        open={state === "review_modal" || state === "launching"}
        draft={editableDraft}
        onClose={closeReview}
        onUpdate={updateDraft}
        onConfirm={launchTask}
        isLaunching={isLaunching}
        isValid={isValidForLaunch()}
      />
    </div>
  );
}
