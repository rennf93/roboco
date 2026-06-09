"use client";

import { Loader2, Sparkles } from "lucide-react";
import { usePrompter } from "@/hooks/use-prompter";
import {
  ChatMessages,
  ChatComposer,
  ConfirmDialog,
  SuccessCard,
  IntakeForm,
} from "@/components/prompter";

export default function PrompterPage() {
  const {
    state,
    messages,
    isSending,
    activity,
    editableDraft,
    createdTaskId,
    createdTaskTitle,
    createdTaskTeam,
    targetKind,
    setTargetKind,
    projectId,
    setProjectId,
    productId,
    setProductId,
    initialMessage,
    setInitialMessage,
    isFormValid,
    start,
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

  const showForm = state === "form" || state === "preparing";
  const isComposerDisabled =
    state === "launching" || state === "success" || isSending;

  return (
    <div className="flex h-full flex-col">
      {/* Page header */}
      <div className="flex items-center gap-3 border-b px-6 py-4">
        <Sparkles className="h-5 w-5 text-primary" />
        <div>
          <h1 className="text-lg font-semibold">Task Assistant</h1>
          <p className="text-xs text-muted-foreground">
            Chat with an agent that reads your code and drafts the task
          </p>
        </div>
      </div>

      {showForm ? (
        <IntakeForm
          targetKind={targetKind}
          onTargetKind={setTargetKind}
          projectId={projectId}
          onProjectId={setProjectId}
          productId={productId}
          onProductId={setProductId}
          initialMessage={initialMessage}
          onInitialMessage={setInitialMessage}
          isValid={isFormValid()}
          isPreparing={state === "preparing"}
          onStart={start}
        />
      ) : (
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

          {/* Live activity indicator — "watch it work" */}
          {activity && state !== "success" && (
            <div className="flex items-center gap-2 px-6 py-1.5 text-xs text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" />
              {activity}
            </div>
          )}

          {/* Composer */}
          {state !== "success" && (
            <ChatComposer
              onSend={send}
              disabled={isComposerDisabled}
              isSending={isSending}
            />
          )}
        </div>
      )}

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
