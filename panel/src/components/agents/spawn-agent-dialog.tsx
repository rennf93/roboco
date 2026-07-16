"use client";

import { useRef, useState } from "react";
import { useSpawnAgent } from "@/hooks/use-agents";
import { getErrorMessage } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
  DialogDescription,
} from "@/components/ui/dialog";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { HelpTip } from "@/components/ui/help-tip";
import { Play } from "lucide-react";
import { toast } from "sonner";

interface SpawnAgentDialogProps {
  agentId: string;
  agentName: string;
  trigger?: React.ReactNode;
}

export function SpawnAgentDialog({
  agentId,
  agentName,
  trigger,
}: SpawnAgentDialogProps) {
  const [open, setOpen] = useState(false);
  const [taskId, setTaskId] = useState("");
  const [initialPrompt, setInitialPrompt] = useState("");
  const spawnAgent = useSpawnAgent();
  // Synchronous re-entrancy guard: `spawnAgent.isPending` only flips on a
  // re-render, which lags a fast double-click/double-fire by a tick or two —
  // the guard below blocks a second call within the same synchronous burst
  // regardless of render timing.
  const submittingRef = useRef(false);

  const handleSpawn = async () => {
    if (submittingRef.current) return;
    submittingRef.current = true;
    try {
      const result = await spawnAgent.mutateAsync({
        agentId,
        request: {
          task_id: taskId || undefined,
          initial_prompt: initialPrompt || undefined,
        },
      });
      if (result.already_running) {
        toast.info(`Agent ${agentName} already running — spawn skipped`);
      } else {
        toast.success(`Agent ${agentName} spawned successfully`);
      }
      setOpen(false);
      resetForm();
    } catch (error) {
      toast.error(getErrorMessage(error));
    } finally {
      submittingRef.current = false;
    }
  };

  const resetForm = () => {
    setTaskId("");
    setInitialPrompt("");
  };

  // The tooltip must wrap the DialogTrigger, never sit inside it: with
  // HelpTip as DialogTrigger's asChild child, the dialog's click handler is
  // cloned onto the Tooltip root (not a DOM element) and silently dropped.
  const defaultTrigger = (
    <DropdownMenuItem onSelect={(e) => e.preventDefault()}>
      <Play className="h-4 w-4 mr-2" />
      Spawn
    </DropdownMenuItem>
  );

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      {trigger ? (
        <DialogTrigger asChild>{trigger}</DialogTrigger>
      ) : (
        <HelpTip
          label="Start this agent's container, optionally pre-claiming a task"
          side="left"
        >
          <DialogTrigger asChild>{defaultTrigger}</DialogTrigger>
        </HelpTip>
      )}
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Spawn {agentName}</DialogTitle>
          <DialogDescription>
            Start this agent with optional task assignment and initial prompt.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <HelpTip label="Pre-claims this task on spawn instead of pulling from the pool">
              <Label htmlFor="taskId" className="w-fit">Task ID (optional)</Label>
            </HelpTip>
            <Input
              id="taskId"
              value={taskId}
              onChange={(e) => setTaskId(e.target.value)}
              placeholder="UUID of task to assign"
            />
          </div>
          <div className="space-y-2">
            <HelpTip label="Extra instructions passed to the agent's first turn">
              <Label htmlFor="initialPrompt" className="w-fit">Initial Prompt (optional)</Label>
            </HelpTip>
            <Input
              id="initialPrompt"
              value={initialPrompt}
              onChange={(e) => setInitialPrompt(e.target.value)}
              placeholder="Initial instructions for the agent"
            />
          </div>
          <div className="flex justify-end gap-2">
            <HelpTip label="Closes without spawning">
              <Button variant="outline" onClick={() => setOpen(false)}>
                Cancel
              </Button>
            </HelpTip>
            <HelpTip label="Already-running agents are skipped — no duplicate container is started">
              <span>
                <Button onClick={handleSpawn} disabled={spawnAgent.isPending}>
                  {spawnAgent.isPending ? "Spawning..." : "Spawn Agent"}
                </Button>
              </span>
            </HelpTip>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
