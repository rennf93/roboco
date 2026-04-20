"use client";

import { useState } from "react";
import { useSpawnAgent } from "@/hooks/use-agents";
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
import { Play } from "lucide-react";
import { toast } from "sonner";

interface SpawnAgentDialogProps {
  agentId: string;
  agentName: string;
  trigger?: React.ReactNode;
}

export function SpawnAgentDialog({ agentId, agentName, trigger }: SpawnAgentDialogProps) {
  const [open, setOpen] = useState(false);
  const [taskId, setTaskId] = useState("");
  const [initialPrompt, setInitialPrompt] = useState("");
  const spawnAgent = useSpawnAgent();

  const handleSpawn = async () => {
    try {
      await spawnAgent.mutateAsync({
        agentId,
        request: {
          task_id: taskId || undefined,
          initial_prompt: initialPrompt || undefined,
        },
      });
      toast.success(`Agent ${agentName} spawned successfully`);
      setOpen(false);
      resetForm();
    } catch {
      toast.error("Failed to spawn agent");
    }
  };

  const resetForm = () => {
    setTaskId("");
    setInitialPrompt("");
  };

  const defaultTrigger = (
    <DropdownMenuItem onSelect={(e) => e.preventDefault()}>
      <Play className="h-4 w-4 mr-2" />
      Spawn
    </DropdownMenuItem>
  );

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        {trigger || defaultTrigger}
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Spawn {agentName}</DialogTitle>
          <DialogDescription>
            Start this agent with optional task assignment and initial prompt.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="taskId">Task ID (optional)</Label>
            <Input
              id="taskId"
              value={taskId}
              onChange={(e) => setTaskId(e.target.value)}
              placeholder="UUID of task to assign"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="initialPrompt">Initial Prompt (optional)</Label>
            <Input
              id="initialPrompt"
              value={initialPrompt}
              onChange={(e) => setInitialPrompt(e.target.value)}
              placeholder="Initial instructions for the agent"
            />
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handleSpawn} disabled={spawnAgent.isPending}>
              {spawnAgent.isPending ? "Spawning..." : "Spawn Agent"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
