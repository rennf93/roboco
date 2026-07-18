"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { AgentSelector } from "@/components/agents/agent-selector";
import { HelpTip } from "@/components/ui/help-tip";
import { AgentRole } from "@/types";
import { MessageSquarePlus } from "lucide-react";
import { toast } from "sonner";
import { getAgentDisplayName } from "@/lib/agent-utils";
import { getErrorMessage } from "@/lib/api/client";
import { useCreateCeoConversation } from "@/hooks/use-a2a-live";

// Self, plus every role that can't actually read/answer a DM: auditor and
// pr_reviewer carry no read_a2a on their manifests, prompter and secretary
// are human-only note/evidence roles — a DM to any of them is a black hole.
// Exported so other "start a fresh 1:1" surfaces (the /tg Mini App chat tab)
// share the exact same exclusion list instead of drifting out of sync.
export const EXCLUDE_NON_DM_ROLES = [
  AgentRole.CEO,
  AgentRole.AUDITOR,
  AgentRole.PR_REVIEWER,
  AgentRole.PROMPTER,
  AgentRole.SECRETARY,
];

interface A2ANewDmDialogProps {
  /** Called with the new (or reopened) conversation's id once the CEO's
   * first message is sent — the caller selects/opens it in the page. */
  onCreated: (conversationId: string) => void;
  /** Controlled-open pair — omit both for the default uncontrolled trigger-
   * button behavior (internal state). Pass both to drive the dialog from
   * outside, e.g. the agent card's DM quick-action deep link. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** Preselects this agent as the target whenever the dialog opens. */
  initialTarget?: string | null;
}

/**
 * CEO-voiced "start a fresh 1:1" entry point — the org-chart switchboard and
 * classic list only ever show conversations that already exist; this is the
 * one surface that creates one, addressed to any agent (never itself).
 */
export function A2ANewDmDialog({
  onCreated,
  open: openProp,
  onOpenChange,
  initialTarget,
}: A2ANewDmDialogProps) {
  const [internalOpen, setInternalOpen] = useState(false);
  const open = openProp ?? internalOpen;
  const setOpen = onOpenChange ?? setInternalOpen;
  const [targetAgent, setTargetAgent] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const create = useCreateCeoConversation();

  const resetForm = () => {
    setTargetAgent(null);
    setMessage("");
  };

  // Preselect on the open transition (render-phase adjustment, not an
  // effect — same idiom as the connection-banner reset in a2a-view.tsx).
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open && initialTarget) setTargetAgent(initialTarget);
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = message.trim();
    if (!targetAgent || !trimmed || create.isPending) return;

    create.mutate(
      { target_agent: targetAgent, initial_message: trimmed },
      {
        onSuccess: (conversation) => {
          toast.success(
            `Started a DM with ${getAgentDisplayName(targetAgent)}`,
          );
          setOpen(false);
          resetForm();
          onCreated(conversation.id);
        },
        onError: (error) => {
          toast.error(getErrorMessage(error));
        },
      },
    );
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(newOpen) => {
        setOpen(newOpen);
        if (!newOpen) resetForm();
      }}
    >
      <HelpTip label="Open a fresh 1:1 conversation with any agent, as the CEO">
        <DialogTrigger asChild>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-7 gap-1 px-2 text-xs"
          >
            <MessageSquarePlus className="h-3.5 w-3.5" />
            New DM
          </Button>
        </DialogTrigger>
      </HelpTip>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New direct message</DialogTitle>
          <DialogDescription>
            Starts (or reopens) your own 1:1 with an agent — separate from
            the threads you&apos;re watching, and visible only to you and
            them.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <HelpTip label="Every agent may A2A the CEO in reply, but only the CEO may start a thread — pick who to open one with">
              <Label>Agent</Label>
            </HelpTip>
            <AgentSelector
              value={targetAgent}
              onChange={setTargetAgent}
              excludeRoles={EXCLUDE_NON_DM_ROLES}
              placeholder="Select an agent..."
              allowClear={false}
            />
          </div>
          <div className="space-y-2">
            <HelpTip label="Required to start the conversation — the agent sees this the moment it's sent">
              <Label htmlFor="new-dm-message">First message</Label>
            </HelpTip>
            <Textarea
              id="new-dm-message"
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              placeholder="What do you want to say?"
              className="min-h-[100px] resize-none"
              disabled={create.isPending}
            />
          </div>
          <DialogFooter>
            <HelpTip label="Sends as the CEO — opens the conversation and delivers this message in one step">
              <span>
                <Button
                  type="submit"
                  disabled={!targetAgent || !message.trim() || create.isPending}
                >
                  Start conversation
                </Button>
              </span>
            </HelpTip>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
