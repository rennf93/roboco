"use client";

import { useState } from "react";
import { useResolveWait } from "@/hooks/use-agents";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
  DialogDescription,
} from "@/components/ui/dialog";
import { HelpTip } from "@/components/ui/help-tip";
import { Send } from "lucide-react";
import { toast } from "sonner";

interface ResolveWaitDialogProps {
  agentId: string;
}

export function ResolveWaitDialog({ agentId }: ResolveWaitDialogProps) {
  const [open, setOpen] = useState(false);
  const [resolution, setResolution] = useState("");
  const resolveWait = useResolveWait();

  const handleResolve = async () => {
    if (!resolution.trim()) {
      toast.error("Please provide a resolution");
      return;
    }

    try {
      await resolveWait.mutateAsync({ agentId, resolution });
      toast.success("Resolution sent to agent");
      setOpen(false);
      setResolution("");
    } catch {
      toast.error("Failed to resolve wait");
    }
  };

  const handleOpenChange = (newOpen: boolean) => {
    if (!newOpen) setResolution("");
    setOpen(newOpen);
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <HelpTip label="Send the agent the information it needs to unblock and resume">
        <DialogTrigger asChild>
          <Button>
            <Send className="h-4 w-4 mr-2" />
            Resolve Wait
          </Button>
        </DialogTrigger>
      </HelpTip>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Resolve Agent Wait</DialogTitle>
          <DialogDescription>
            Provide context or instructions to help the agent continue.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <HelpTip label="Delivered to the agent verbatim as the answer to what it's waiting on">
              <Label htmlFor="resolution" className="w-fit">Resolution Message</Label>
            </HelpTip>
            <Textarea
              id="resolution"
              value={resolution}
              onChange={(e) => setResolution(e.target.value)}
              placeholder="Provide the information or decision the agent needs to continue..."
              rows={4}
            />
          </div>
          <div className="flex justify-end gap-2">
            <HelpTip label="Closes without sending anything to the agent">
              <Button variant="outline" onClick={() => setOpen(false)}>
                Cancel
              </Button>
            </HelpTip>
            <HelpTip label="Delivers the message above to the agent so it can resume">
              <span>
                <Button onClick={handleResolve} disabled={resolveWait.isPending}>
                  {resolveWait.isPending ? "Sending..." : "Send Resolution"}
                </Button>
              </span>
            </HelpTip>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
