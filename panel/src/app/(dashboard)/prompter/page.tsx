"use client";

import { useEffect, useRef } from "react";
import { Loader2, Sparkles, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { usePrompter } from "@/hooks/use-prompter";
import { Team } from "@/types";
import {
  ChatMessages,
  ChatComposer,
  SuccessCard,
  BoardReviewSentCard,
  IntakeForm,
  BatchReviewCard,
} from "@/components/prompter";

export default function PrompterPage() {
  const {
    state,
    messages,
    isSending,
    activity,
    createdTaskId,
    createdTaskTitle,
    createdTaskTeam,
    targetKind,
    setTargetKind,
    projectId,
    setProjectId,
    productId,
    setProductId,
    projectIds,
    setProjectIds,
    initialMessage,
    setInitialMessage,
    isFormValid,
    start,
    send,
    keepChatting,
    launchTask,
    startAnother,
    isLaunching,
    startRedraft,
    batch,
    batchWaves,
    batchResult,
    setBatchDraftProjects,
    confirmBatch,
  } = usePrompter();

  // Entry from a task's "Re-draft with board feedback" button: ?redraft=<taskId>
  // re-opens intake seeded with the board's review of that task. Fire once.
  const redraftTriggered = useRef(false);
  useEffect(() => {
    if (redraftTriggered.current) return;
    const taskId = new URLSearchParams(window.location.search).get("redraft");
    if (taskId) {
      redraftTriggered.current = true;
      void startRedraft(taskId);
    }
  }, [startRedraft]);

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
        {/* End chat — reap the agent and return to the form (any chat state) */}
        {!showForm && state !== "success" && (
          <Button
            variant="ghost"
            size="sm"
            className="ml-auto text-muted-foreground"
            onClick={startAnother}
            disabled={state === "launching"}
          >
            <X className="mr-1 h-4 w-4" />
            End chat
          </Button>
        )}
      </div>

      {showForm ? (
        <IntakeForm
          targetKind={targetKind}
          onTargetKind={setTargetKind}
          projectId={projectId}
          onProjectId={setProjectId}
          productId={productId}
          onProductId={setProductId}
          projectIds={projectIds}
          onProjectIds={setProjectIds}
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
              <div className="w-full max-w-md space-y-3">
                {/* Board-routed MegaTask: created HELD for PO+HoM review, not
                    dispatched — the CEO releases it with Approve & Start on the
                    umbrella task. Every other path is a real "created/launched"
                    success. ``createdTaskTeam === BOARD`` is set only by the
                    batch board route (the single-draft board route parks and
                    never reaches success). */}
                {createdTaskTeam === Team.BOARD && batchResult ? (
                  <BoardReviewSentCard
                    taskId={createdTaskId}
                    taskTitle={createdTaskTitle}
                    rootSubtaskCount={batchResult.root_subtask_ids.length}
                    waveCount={batchResult.waves.length}
                    onStartAnother={startAnother}
                  />
                ) : (
                  <>
                    <SuccessCard
                      taskId={createdTaskId}
                      taskTitle={createdTaskTitle}
                      team={createdTaskTeam}
                      onStartAnother={startAnother}
                    />
                    {batchResult && (
                      <p className="text-center text-xs text-muted-foreground">
                        {batchResult.root_subtask_ids.length} tasks sequenced
                        into {batchResult.waves.length} wave
                        {batchResult.waves.length === 1 ? "" : "s"}.
                        {batchResult.warnings.length > 0 &&
                          ` ${batchResult.warnings.length} advisory note${
                            batchResult.warnings.length === 1 ? "" : "s"
                          }.`}
                      </p>
                    )}
                  </>
                )}
              </div>
            </div>
          ) : state === "batch_preview" && batch ? (
            /* MegaTask review — the agent proposed a batch; confirm them
               together. Owns the scroll area (min-h-0 + overflow-y-auto) so a
               tall batch — many tasks, per-cell pickers, the wave plan — scrolls
               instead of overflowing the clipped parent and stranding the launch
               buttons off-screen. */
            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
              <BatchReviewCard
                batch={batch}
                waves={batchWaves}
                projectIds={projectIds}
                onKeepChatting={keepChatting}
                onSetProjects={setBatchDraftProjects}
                onConfirm={confirmBatch}
                isLaunching={isLaunching}
              />
            </div>
          ) : (
            <ChatMessages
              messages={messages}
              onStart={launchTask}
              onKeepChatting={keepChatting}
              isLaunching={isLaunching}
            />
          )}

          {/* Live activity indicator — "watch it work" (prominent) */}
          {activity && state !== "success" && (
            <div className="mx-4 mb-2 flex items-center gap-2.5 rounded-lg border border-primary/30 bg-primary/10 px-4 py-2.5 text-sm font-medium text-primary">
              <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
              <span>{activity}</span>
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
    </div>
  );
}
